"""Actions groupées de l'écran Fichiers (source « disque ») : .zip, annuler une suppression."""

import io
import os
import zipfile

import pytest
from fastapi.testclient import TestClient

import main
from services import disk_browser, trash, zip_download

H = {"X-Jabba": "1"}


@pytest.fixture
def disque(tmp_path, monkeypatch):
    root = tmp_path / "partage"
    (root / "Photos" / "Été").mkdir(parents=True)
    (root / "Photos" / "a.jpg").write_bytes(b"A" * 10)
    (root / "Photos" / "Été" / "b.jpg").write_bytes(b"B" * 20)
    (root / "notes.txt").write_text("bonjour")
    secret = tmp_path / "secret.txt"
    secret.write_text("hors du partage")
    os.symlink(secret, root / "Photos" / "lien-vers-secret.txt")
    os.symlink(tmp_path, root / "Photos" / "lien-dossier")
    monkeypatch.setenv("JABBA_DEMO_SOURCE", "disque")
    monkeypatch.setenv("DOSSIERS_DISQUE", f"Partage={root}")
    monkeypatch.setattr(disk_browser, "TRASH_NAME", str(tmp_path / "corbeille"))
    with TestClient(main.app, base_url="https://testserver") as client:
        client.cookies.clear()
        r = client.post(
            "/api/admin/connexion", json={"username": "serveur-demo", "password": "demo-mot-de-passe"}, headers=H
        )
        assert r.status_code == 200, r.text
        yield client, root


def zip_content(answer):
    with zipfile.ZipFile(io.BytesIO(answer.content)) as archive:
        return {nom: archive.read(nom) for nom in archive.namelist()}


def test_zip_of_a_folder_and_a_file_without_following_links(disque):
    client, _ = disque
    r = client.get("/api/fichiers/zip", params=[("chemin", "/Partage/Photos"), ("chemin", "/Partage/notes.txt")])
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "Partage%20%282%20%C3%A9l%C3%A9ments%29.zip" in r.headers["content-disposition"]
    assert zip_content(r) == {"Photos/a.jpg": b"A" * 10, "Photos/Été/b.jpg": b"B" * 20, "notes.txt": b"bonjour"}


def test_zip_of_a_single_folder(disque):
    client, _ = disque
    r = client.get("/api/fichiers/zip", params={"chemin": "/Partage/Photos/Été"})
    assert "%C3%89t%C3%A9.zip" in r.headers["content-disposition"]
    assert zip_content(r) == {"Été/b.jpg": b"B" * 20}


@pytest.mark.parametrize(
    "chemins, code",
    [([], 400), (["/"], 400), (["/Partage/../../etc"], 404), (["/Partage/absent"], 404), (["/Inconnu/x"], 404)],
)
def test_zip_refused(disque, chemins, code):
    client, _ = disque
    r = client.get("/api/fichiers/zip", params=[("chemin", c) for c in chemins])
    assert r.status_code in (code, 400), r.text


def test_zip_too_big_or_disk_full(disque, monkeypatch):
    client, _ = disque
    monkeypatch.setattr(zip_download, "MAX_ZIP_BYTES", 15)
    assert client.get("/api/fichiers/zip", params={"chemin": "/Partage/Photos"}).status_code == 413
    monkeypatch.setattr(zip_download, "MAX_ZIP_BYTES", 10**12)
    monkeypatch.setattr(zip_download.safe_files, "free_space", lambda dossier: 1024)
    assert client.get("/api/fichiers/zip", params={"chemin": "/Partage/Photos"}).status_code == 507
    monkeypatch.setattr(zip_download, "MAX_FILES", 1)
    assert client.get("/api/fichiers/zip", params={"chemin": "/Partage/Photos"}).status_code == 413


def test_the_temporary_zip_is_deleted(disque, tmp_path, monkeypatch):
    client, _ = disque
    dossier = tmp_path / "tmp"
    dossier.mkdir()
    monkeypatch.setattr(zip_download.tempfile, "gettempdir", lambda: str(dossier))
    client.get("/api/fichiers/zip", params={"chemin": "/Partage/notes.txt"})
    assert list(dossier.iterdir()) == []
    monkeypatch.setattr(zip_download, "MAX_FILES", 0)
    client.get("/api/fichiers/zip", params={"chemin": "/Partage/notes.txt"})
    assert list(dossier.iterdir()) == []  # même quand on refuse


def test_undo_a_deletion(disque):
    client, root = disque
    for chemin, dossier in (
        ("/Partage/notes.txt", False),
        ("/Partage/Photos/a.jpg", False),
        ("/Partage/Photos/Été", True),
    ):
        assert (
            client.post("/api/fichiers/supprimer", json={"path": chemin, "folder": dossier}, headers=H).status_code
            == 200
        )
    assert not (root / "notes.txt").exists()
    r = client.post(
        "/api/fichiers/annuler-suppression",
        json={"paths": ["/Partage/notes.txt", "/Partage/Photos/Été", "/Partage/jamais-supprime"]},
        headers=H,
    )
    assert r.status_code == 200 and sorted(r.json()["restored"]) == ["/Partage/Photos/Été", "/Partage/notes.txt"]
    assert (root / "notes.txt").read_text() == "bonjour" and (root / "Photos" / "Été" / "b.jpg").exists()
    assert not (root / "Photos" / "a.jpg").exists()  # pas demandé : reste à la corbeille
    assert (
        client.post("/api/fichiers/annuler-suppression", json={"paths": ["/Partage/notes.txt"]}, headers=H).status_code
        == 404
    )
    assert client.post("/api/fichiers/annuler-suppression", json={"paths": []}, headers=H).status_code == 400


def test_undo_takes_back_the_last_deletion(disque, monkeypatch):
    client, root = disque
    client.post("/api/fichiers/supprimer", json={"path": "/Partage/notes.txt"}, headers=H)
    (root / "notes.txt").write_text("deuxième")
    client.post("/api/fichiers/supprimer", json={"path": "/Partage/notes.txt"}, headers=H)
    client.post("/api/fichiers/annuler-suppression", json={"paths": ["/Partage/notes.txt"]}, headers=H)
    assert (root / "notes.txt").read_text() == "deuxième"
    # au-delà de 10 minutes, l'annulation ne retrouve plus rien
    monkeypatch.setattr(trash, "UNDO_WINDOW_SECONDS", -1)
    assert (
        client.post("/api/fichiers/annuler-suppression", json={"paths": ["/Partage/notes.txt"]}, headers=H).status_code
        == 404
    )


def test_original_path_depends_on_the_source():
    assert trash.original_path({"source": "disque", "origin": "/A/B", "name": "c.txt"}) == "/A/B/c.txt"
    assert trash.original_path({"source": "disque", "origin": "/", "name": "c.txt"}) == "/c.txt"
    assert trash.original_path({"source": "disque", "origin": None, "name": "c.txt"}) == ""
    assert trash.original_path({"source": "nextcloud", "origin": "/A/c.txt", "name": "c.txt"}) == "/A/c.txt"
