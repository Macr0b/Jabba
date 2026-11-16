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
    racine = tmp_path / "partage"
    (racine / "Photos" / "Été").mkdir(parents=True)
    (racine / "Photos" / "a.jpg").write_bytes(b"A" * 10)
    (racine / "Photos" / "Été" / "b.jpg").write_bytes(b"B" * 20)
    (racine / "notes.txt").write_text("bonjour")
    secret = tmp_path / "secret.txt"
    secret.write_text("hors du partage")
    os.symlink(secret, racine / "Photos" / "lien-vers-secret.txt")
    os.symlink(tmp_path, racine / "Photos" / "lien-dossier")
    monkeypatch.setenv("JABBA_DEMO_SOURCE", "disque")
    monkeypatch.setenv("DOSSIERS_DISQUE", f"Partage={racine}")
    monkeypatch.setattr(disk_browser, "TRASH_NAME", str(tmp_path / "corbeille"))
    with TestClient(main.app, base_url="https://testserver") as client:
        client.cookies.clear()
        r = client.post(
            "/api/admin/connexion", json={"identifiant": "serveur-demo", "mot_de_passe": "demo-mot-de-passe"}, headers=H
        )
        assert r.status_code == 200, r.text
        yield client, racine


def contenu_zip(reponse):
    with zipfile.ZipFile(io.BytesIO(reponse.content)) as archive:
        return {nom: archive.read(nom) for nom in archive.namelist()}


def test_zip_d_un_dossier_et_d_un_fichier_sans_suivre_les_liens(disque):
    client, _ = disque
    r = client.get("/api/fichiers/zip", params=[("chemin", "/Partage/Photos"), ("chemin", "/Partage/notes.txt")])
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "Partage%20%282%20%C3%A9l%C3%A9ments%29.zip" in r.headers["content-disposition"]
    assert contenu_zip(r) == {"Photos/a.jpg": b"A" * 10, "Photos/Été/b.jpg": b"B" * 20, "notes.txt": b"bonjour"}


def test_zip_d_un_seul_dossier(disque):
    client, _ = disque
    r = client.get("/api/fichiers/zip", params={"chemin": "/Partage/Photos/Été"})
    assert "%C3%89t%C3%A9.zip" in r.headers["content-disposition"]
    assert contenu_zip(r) == {"Été/b.jpg": b"B" * 20}


@pytest.mark.parametrize(
    "chemins, code",
    [([], 400), (["/"], 400), (["/Partage/../../etc"], 404), (["/Partage/absent"], 404), (["/Inconnu/x"], 404)],
)
def test_zip_refuse(disque, chemins, code):
    client, _ = disque
    r = client.get("/api/fichiers/zip", params=[("chemin", c) for c in chemins])
    assert r.status_code in (code, 400), r.text


def test_zip_trop_gros_ou_disque_plein(disque, monkeypatch):
    client, _ = disque
    monkeypatch.setattr(zip_download, "MAX_ZIP_BYTES", 15)
    assert client.get("/api/fichiers/zip", params={"chemin": "/Partage/Photos"}).status_code == 413
    monkeypatch.setattr(zip_download, "MAX_ZIP_BYTES", 10**12)
    monkeypatch.setattr(zip_download.safe_files, "free_space", lambda dossier: 1024)
    assert client.get("/api/fichiers/zip", params={"chemin": "/Partage/Photos"}).status_code == 507
    monkeypatch.setattr(zip_download, "MAX_FILES", 1)
    assert client.get("/api/fichiers/zip", params={"chemin": "/Partage/Photos"}).status_code == 413


def test_le_zip_temporaire_est_efface(disque, tmp_path, monkeypatch):
    client, _ = disque
    dossier = tmp_path / "tmp"
    dossier.mkdir()
    monkeypatch.setattr(zip_download.tempfile, "gettempdir", lambda: str(dossier))
    client.get("/api/fichiers/zip", params={"chemin": "/Partage/notes.txt"})
    assert list(dossier.iterdir()) == []
    monkeypatch.setattr(zip_download, "MAX_FILES", 0)
    client.get("/api/fichiers/zip", params={"chemin": "/Partage/notes.txt"})
    assert list(dossier.iterdir()) == []  # même quand on refuse


def test_annuler_une_suppression(disque):
    client, racine = disque
    for chemin, dossier in (
        ("/Partage/notes.txt", False),
        ("/Partage/Photos/a.jpg", False),
        ("/Partage/Photos/Été", True),
    ):
        assert (
            client.post("/api/fichiers/supprimer", json={"chemin": chemin, "dossier": dossier}, headers=H).status_code
            == 200
        )
    assert not (racine / "notes.txt").exists()
    r = client.post(
        "/api/fichiers/annuler-suppression",
        json={"chemins": ["/Partage/notes.txt", "/Partage/Photos/Été", "/Partage/jamais-supprime"]},
        headers=H,
    )
    assert r.status_code == 200 and sorted(r.json()["restaures"]) == ["/Partage/Photos/Été", "/Partage/notes.txt"]
    assert (racine / "notes.txt").read_text() == "bonjour" and (racine / "Photos" / "Été" / "b.jpg").exists()
    assert not (racine / "Photos" / "a.jpg").exists()  # pas demandé : reste à la corbeille
    assert (
        client.post(
            "/api/fichiers/annuler-suppression", json={"chemins": ["/Partage/notes.txt"]}, headers=H
        ).status_code
        == 404
    )
    assert client.post("/api/fichiers/annuler-suppression", json={"chemins": []}, headers=H).status_code == 400


def test_annuler_reprend_la_derniere_suppression(disque, monkeypatch):
    client, racine = disque
    client.post("/api/fichiers/supprimer", json={"chemin": "/Partage/notes.txt"}, headers=H)
    (racine / "notes.txt").write_text("deuxième")
    client.post("/api/fichiers/supprimer", json={"chemin": "/Partage/notes.txt"}, headers=H)
    client.post("/api/fichiers/annuler-suppression", json={"chemins": ["/Partage/notes.txt"]}, headers=H)
    assert (racine / "notes.txt").read_text() == "deuxième"
    # au-delà de 10 minutes, l'annulation ne retrouve plus rien
    monkeypatch.setattr(trash, "UNDO_WINDOW_SECONDS", -1)
    assert (
        client.post(
            "/api/fichiers/annuler-suppression", json={"chemins": ["/Partage/notes.txt"]}, headers=H
        ).status_code
        == 404
    )


def test_chemin_d_origine_selon_la_source():
    assert trash.original_path({"source": "disque", "origine": "/A/B", "nom": "c.txt"}) == "/A/B/c.txt"
    assert trash.original_path({"source": "disque", "origine": "/", "nom": "c.txt"}) == "/c.txt"
    assert trash.original_path({"source": "disque", "origine": None, "nom": "c.txt"}) == ""
    assert trash.original_path({"source": "nextcloud", "origine": "/A/c.txt", "nom": "c.txt"}) == "/A/c.txt"
