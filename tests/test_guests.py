"""La porte du Wi-Fi (page invités) : séparée de l'appli et de ses données (revue, point 1)."""

import json
import os

import pytest
import yaml
from conftest import ROOT
from fastapi.testclient import TestClient

import guest_door
from services import activity_log, guests

H = {"X-Jabba": "1"}


@pytest.fixture
def etat(tmp_path, monkeypatch):
    f = tmp_path / "porte-wifi" / "etat.json"
    monkeypatch.setattr(guests, "STATE_FILE", str(f))
    monkeypatch.setattr(guests, "OLD_STATE_FILE", str(tmp_path / "invites.json"))
    share = tmp_path / "partage"
    share.mkdir()
    (share / "menu.txt").write_text("menu")
    guests.prepare_state()
    guests.update(
        lambda d: d.update(
            active=True,
            code="1234",
            server="serveur-test",
            folders=[{"id": "p", "name": "Partage", "path": str(share), "mode": "depot"}],
        )
    )
    return f, share


def wifi(ip="192.168.1.20"):
    """La porte du Wi-Fi, vue depuis l'adresse `ip`."""

    async def from_address(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope, client=(ip, 5000))
        await guest_door.app(scope, receive, send)

    return TestClient(from_address)


def test_state_created_once_and_kept(etat):
    f, _ = etat
    d = json.loads(f.read_text())
    assert d["code"] == "1234" and len(d["secret"]) == 64
    assert oct(os.stat(f).st_mode & 0o777) == "0o600"
    assert oct(os.stat(f.parent).st_mode & 0o777) == "0o700"


def test_old_location_taken_over(tmp_path, monkeypatch):
    old = tmp_path / "invites.json"
    old.write_text(json.dumps({"code": "9876", "secret": "s" * 64}))
    monkeypatch.setattr(guests, "STATE_FILE", str(tmp_path / "porte-wifi" / "etat.json"))
    monkeypatch.setattr(guests, "OLD_STATE_FILE", str(old))
    guests.prepare_state()
    assert guests.read()["code"] == "9876" and not old.exists()


def test_guest_journey(etat):
    c = wifi()
    assert c.get("/api/moi").json()["server"] == "serveur-test"
    assert c.post("/api/invite/entrer", json={"code": "0000"}, headers=H).status_code == 403
    assert c.post("/api/invite/entrer", json={"code": "1234"}, headers=H).status_code == 200
    r = c.get("/api/invite/fichiers", params={"d": "p", "chemin": "/"})
    assert [e["name"] for e in r.json()["items"]] == ["menu.txt"]
    r = c.post("/api/invite/envoyer", params={"d": "p"}, files={"fichiers": ("../../evil.txt", b"x")}, headers=H)
    assert r.json()["sent"] == ["evil.txt"]


def test_no_way_out_of_the_folder(etat):
    c = wifi()
    c.post("/api/invite/entrer", json={"code": "1234"}, headers=H)
    for chemin in ("/../..", "/..", "/.cache", "/%2e%2e"):
        assert c.get("/api/invite/fichiers", params={"d": "p", "chemin": chemin}).status_code in (400, 404)


def test_administration_impossible(etat):
    c = wifi()
    assert c.get("/api/etat").status_code == 404
    assert c.post("/api/admin/connexion", json={}, headers=H).status_code == 404


def test_outside_local_network_refused(etat):
    assert wifi("8.8.8.8").get("/api/invite/etat").status_code == 403


def test_too_many_tries(etat):
    c = wifi("192.168.1.99")
    for _ in range(8):
        c.post("/api/invite/entrer", json={"code": "0000"}, headers=H)
    assert c.post("/api/invite/entrer", json={"code": "1234"}, headers=H).status_code == 429


def test_door_log_cannot_pass_itself_off_as_the_app(tmp_path, monkeypatch):
    primary = tmp_path / "journal.jsonl"
    other = tmp_path / "journal-invites.jsonl"
    monkeypatch.setattr(activity_log, "LOG_FILE", str(primary))
    monkeypatch.setattr(activity_log, "OTHER_LOGS", [str(other)])
    activity_log.record("admin", "vrai événement", "Rob")
    other.write_text(
        json.dumps({"t": 1, "family": "admin", "text": "Faux : mot de passe changé", "who": "Rob"})
        + "\n"
        + "pas du json\n"
    )
    events = activity_log.read()
    fake = [e for e in events if e["text"].startswith("Faux")][0]
    assert fake["family"] == "invite" and "who" not in fake
    assert events[0]["text"] == "vrai événement"


def test_door_container_without_powers():
    compose = yaml.safe_load(open(os.path.join(ROOT, "compose.yaml")))
    s = compose["services"]["jabba-invites"]
    assert s.get("privileged") is not True and "pid" not in s and s.get("network_mode") != "host"
    assert s["cap_drop"] == ["ALL"] and "cap_add" not in s
    assert s["read_only"] is True and "no-new-privileges:true" in s["security_opt"]
    assert not str(s["user"]).startswith("0")
    mounts = [v if isinstance(v, str) else f"{v['source']}:{v['target']}" for v in s["volumes"]]
    assert "./donnees/porte-wifi:/etat" in mounts
    assert not any(m.startswith("./donnees:") for m in mounts), "la porte ne doit pas voir les données de l'appli"
    assert "guest_door:app" in " ".join(s["command"])
    app = compose["services"]["jabba-app"]
    assert "guest_door" not in " ".join(app.get("command", []) or [])
    dockerfile = open(os.path.join(ROOT, "Dockerfile")).read()
    assert "guest_door:app" not in dockerfile.split("CMD", 1)[1]


def test_state_outside_the_guest_folders():
    """L'état (code, secret) n'est jamais dans un dossier que les invités voient."""
    import importlib
    import sys

    env = {"JABBA_DATA": "/data", "JABBA_DOSSIER_INVITES": "/srv/data/invites"}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    os.environ.pop("JABBA_INVITES_ETAT", None)
    try:
        m = importlib.reload(sys.modules["services.guests"])
        assert not os.path.commonpath([m.STATE_FILE, "/srv"]) == "/srv"
        assert m.STATE_FILE == "/data/porte-wifi/etat.json"
    finally:
        for k, v in saved.items():
            os.environ[k] = v
        importlib.reload(sys.modules["services.guests"])


def _enter():
    c = wifi()
    c.post("/api/invite/entrer", json={"code": "1234"}, headers=H)
    return c


def test_drop_never_over_a_file(etat):
    _, share = etat
    c = _enter()
    for _ in range(2):
        r = c.post("/api/invite/envoyer", params={"d": "p"}, files={"fichiers": ("menu.txt", b"nouveau")}, headers=H)
        assert r.status_code == 200
    assert (share / "menu.txt").read_text() == "menu"  # l'original n'a pas bougé
    assert (share / "menu (2).txt").read_text() == "nouveau" and (share / "menu (3).txt").exists()


def test_drop_never_follows_a_link(etat, tmp_path):
    """Un raccourci (même cassé) qui porte le nom du fichier envoyé : on écrit à côté, pas à travers."""
    _, share = etat
    outside = tmp_path / "dehors.txt"
    (share / "photo.jpg").symlink_to(outside)
    r = _enter().post("/api/invite/envoyer", params={"d": "p"}, files={"fichiers": ("photo.jpg", b"x")}, headers=H)
    assert r.json()["sent"] == ["photo (2).jpg"] and not outside.exists()


def test_drop_too_big_refused(etat, monkeypatch):
    _, share = etat
    monkeypatch.setattr(guests, "MAX_FILE_BYTES", 10)
    r = _enter().post("/api/invite/envoyer", params={"d": "p"}, files={"fichiers": ("gros.bin", b"x" * 50)}, headers=H)
    assert r.status_code == 413 and not (share / "gros.bin").exists()


def test_drop_always_keeps_free_space(etat, monkeypatch):
    _, share = etat
    monkeypatch.setattr(guests, "KEEP_FREE_BYTES", 10**18)  # plus de place que n'en a aucun disque
    r = _enter().post("/api/invite/envoyer", params={"d": "p"}, files={"fichiers": ("a.txt", b"x")}, headers=H)
    assert r.status_code == 507 and not (share / "a.txt").exists()


def test_uploads_in_progress_on_disk_and_hidden(etat):
    """Les envois en réception vont dans un dossier caché du dossier des invités, pas dans la mémoire."""
    import tempfile

    assert tempfile.gettempdir().endswith(".envois-en-cours")
    assert os.path.basename(tempfile.gettempdir()).startswith(".")


def test_guest_door_interface_rechecked():
    r = wifi().get("/static/js/core/i18n.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
