"""L'appli en démo : plus de terminal (retiré en 0.14), Dockge jamais ouvert au Wi-Fi (point 3)."""

import pytest
from fastapi.testclient import TestClient

import main
from security import accounts
from services import guest_network

H = {"X-Jabba": "1"}


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app, base_url="https://testserver") as c:
        yield c


def log_in_as(client, identifier, password):
    client.cookies.clear()
    r = client.post("/api/admin/connexion", json={"username": identifier, "password": password}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_terminal_is_gone(client):
    """Retiré en 0.14 : un shell complet ne rentre pas dans la liste fermée des actions du serveur."""
    log_in_as(client, "serveur-demo", "demo-mot-de-passe")
    assert client.post("/api/terminal/ticket", headers=H).status_code in (404, 405)


def test_dockge_never_open_to_wifi(client):
    log_in_as(client, "serveur-demo", "demo-mot-de-passe")
    r = client.post("/api/invites", json={"action": "wifi", "outil": "dockge", "wifi": True}, headers=H)
    assert r.status_code == 403
    r = client.post("/api/invites", json={"action": "wifi", "outil": "jellyfin", "wifi": True}, headers=H)
    assert r.status_code == 200


def test_firewall_rules_always_block_dockge():
    ports = guest_network.closed_ports({"wifi": {"dockge": True, "jellyfin": True}})
    assert 5001 in ports  # Dockge n'est jamais ouvert au Wi-Fi, même coché
    assert 8096 not in ports


def test_admin_api_without_session(client):
    client.cookies.clear()
    assert client.get("/api/etat").status_code in (401, 403)


def test_restart_choice(client):
    log_in_as(client, "serveur-demo", "demo-mot-de-passe")
    assert client.get("/api/systeme/redemarrage", headers=H).json()["mode"] in ("auto", "manuel")
    assert client.post("/api/systeme/redemarrage", json={"mode": "manuel"}, headers=H).status_code == 200
    assert client.get("/api/systeme/redemarrage", headers=H).json() == {"mode": "manuel", "chosen": True}
    assert client.post("/api/systeme/redemarrage", json={"mode": "reboot"}, headers=H).status_code == 400
    assert client.post("/api/systeme/redemarrage", json={"mode": "auto"}, headers=H).status_code == 200
    assert client.get("/api/systeme/redemarrage", headers=H).json()["mode"] == "auto"
    from services import activity_log

    assert any("Redémarrage après une mise à jour" in str(e) for e in activity_log.read(20))


def test_restart_choice_for_super_admin_only(client):
    log_in_as(client, "serveur-demo", "demo-mot-de-passe")
    c, _ = accounts.create("simple", "Simple", "admin", None, "test", "admin", set())
    new = "Un-mot-de-passe-solide-43"
    accounts.change_own_password("simple", new)
    log_in_as(client, "simple", new)
    assert client.get("/api/systeme/redemarrage", headers=H).status_code == 200
    assert client.post("/api/systeme/redemarrage", json={"mode": "manuel"}, headers=H).status_code == 403


def test_nextcloud_xml_read_with_the_safe_parser():
    """Une réponse XML piégée (entités en cascade) est refusée au lieu de saturer la mémoire."""
    import pytest
    from defusedxml import EntitiesForbidden

    from services import nextcloud_files

    trap = b"""<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>
<d:multistatus xmlns:d="DAV:"><d:response>&b;</d:response></d:multistatus>"""
    with pytest.raises(EntitiesForbidden):
        nextcloud_files.parse_xml(trap)
    normal = b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>/x</d:href></d:response></d:multistatus>'
    assert nextcloud_files.parse_xml(normal).find("d:response/d:href", nextcloud_files.DAV_NS).text == "/x"


def test_wrong_passwords_block_only_their_author(client, monkeypatch):
    from security import server_password

    monkeypatch.setattr(server_password, "_failures", {})
    client.cookies.clear()
    curious = {**H, "X-Demo-Login": "curieux@exemple.fr"}
    for _ in range(server_password.MAX_ATTEMPTS):
        client.post("/api/admin/connexion", json={"username": "serveur-demo", "password": "faux"}, headers=curious)
    r = client.post("/api/admin/connexion", json={"username": "serveur-demo", "password": "faux"}, headers=curious)
    assert r.status_code == 429
    r = client.post(
        "/api/admin/connexion", json={"username": "serveur-demo", "password": "demo-mot-de-passe"}, headers=H
    )
    assert r.status_code == 200  # l'admin, lui, se connecte normalement


def test_shares_api(client):
    log_in_as(client, "serveur-demo", "demo-mot-de-passe")
    r = client.get("/api/partages", headers=H)
    assert r.status_code == 200 and r.json()["shares"] == [] and set(r.json()["modes"]) == {"lecture", "ecriture"}
    r = client.post("/api/partages", json={"path": "/R2-D2/Projets", "group": "membres", "mode": "lecture"}, headers=H)
    assert r.status_code == 409  # pas de Nextcloud dans la démo
    assert client.post("/api/partages/inconnu/arreter", headers=H).status_code == 400


def test_interface_rechecked_on_every_open(client):
    """Après une mise à jour, le navigateur ne garde pas d'anciens modules JS : il redemande (304 si inchangé)."""
    r = client.get("/static/js/main.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    etag = r.headers["etag"]
    assert client.get("/static/js/main.js", headers={"If-None-Match": etag}).status_code == 304


def test_interface_addresses_change_with_each_version(client):
    """Une nouvelle version = de nouvelles adresses pour les scripts et les styles (et les imports qui en partent)."""
    from version import VERSION

    page = client.get("/").text
    assert f'src="/static/v{VERSION}/js/main.js"' in page and f'href="/static/v{VERSION}/css/base.css"' in page
    assert 'href="/static/manifest.webmanifest"' in page  # le manifeste garde son adresse (appli installée)
    r = client.get(f"/static/v{VERSION}/js/pages/tools.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert r.content == client.get("/static/js/pages/tools.js").content
    for adresse in ("/static/v1/../../main.py", "/static/v1/%2e%2e/%2e%2e/main.py", "/static/v1/..%2f..%2fmain.py"):
        assert "app.mount" not in client.get(adresse).text  # jamais un fichier hors de l'interface
    assert client.get("/static/vX/js/main.js").status_code == 404  # « vX » n'est pas une version


def test_disk_antivirus_through_the_api(client, tmp_path, monkeypatch):
    """La carte de l'Établi : les disques, cocher pour la semaine, analyser maintenant (démo)."""
    from services import antivirus

    monkeypatch.setattr(antivirus, "STATE_FILE", str(tmp_path / "av.json"))
    log_in_as(client, "serveur-demo", "demo-mot-de-passe")
    disks = client.get("/api/antivirus", headers=H).json()["disks"]
    assert disks[0] == {"path": "/srv/data", "name": "Disque de données", "weekly": True, "scan": None}
    r = client.post("/api/antivirus/disques", json={"path": "/srv/data", "weekly": False}, headers=H)
    assert r.status_code == 200
    assert client.get("/api/antivirus", headers=H).json()["disks"][0]["weekly"] is False
    assert client.post("/api/antivirus/analyser", json={"path": "/etc"}, headers=H).status_code == 404
    assert client.post("/api/antivirus/analyser", json={"path": "/srv/data"}, headers=H).status_code == 200


def test_workshop_log_through_the_api(client, tmp_path, monkeypatch):
    from services import workshop

    monkeypatch.setattr(workshop, "STATE_FILE", str(tmp_path / "atelier.json"))
    log_in_as(client, "serveur-demo", "demo-mot-de-passe")
    assert client.get("/api/atelier", headers=H).status_code == 404  # pas activée
    assert client.post("/api/etabli/atelier/installer", json={}, headers=H).status_code == 200
    machine = client.post("/api/atelier/machines", json={"name": "Laser"}, headers=H).json()
    r = client.post(f"/api/atelier/machines/{machine['id']}/journal", json={"title": "Miroir nettoyé"}, headers=H)
    assert r.status_code == 200
    assert (
        client.post(f"/api/atelier/machines/{machine['id']}/journal", json={"title": ""}, headers=H).status_code == 400
    )
    assert client.get("/api/atelier/machines/inconnue", headers=H).status_code == 404
    assert client.get("/api/atelier", headers=H).json()["machines"][0]["entries"] == 1


def test_server_version_in_every_answer(client):
    """Sert aux fenêtres restées ouvertes pendant une mise à jour : elles se rechargent seules (core/api.js)."""
    from version import VERSION

    assert client.get("/api/moi", headers=H).headers["X-Jabba-Version"] == VERSION
    assert client.get("/", headers=H).headers["X-Jabba-Version"] == VERSION


def test_a_service_error_becomes_a_readable_answer(client):
    """Les services lèvent ServiceError (sans dépendre du serveur web) ; l'appli en fait {"detail": …} et le code."""
    log_in_as(client, "serveur-demo", "demo-mot-de-passe")
    r = client.post("/api/outils/outil-qui-n-existe-pas/demarrer", headers=H)
    assert r.status_code == 404
    assert r.json() == {"detail": "Outil inconnu"}


def test_services_do_not_depend_on_the_web_server():
    import os

    dossier = os.path.join(os.path.dirname(main.__file__), "services")
    culprits = [
        f for f in os.listdir(dossier) if f.endswith(".py") and "fastapi" in open(os.path.join(dossier, f)).read()
    ]
    assert not culprits
