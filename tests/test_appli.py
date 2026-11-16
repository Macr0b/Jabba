"""L'appli en démo : terminal réservé au compte du serveur (point 2), Dockge jamais ouvert au Wi-Fi (point 3)."""

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


def connecter(client, ident, mdp):
    client.cookies.clear()
    r = client.post("/api/admin/connexion", json={"identifiant": ident, "mot_de_passe": mdp}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def test_terminal_pour_le_compte_du_serveur(client):
    connecter(client, "serveur-demo", "demo-mot-de-passe")
    r = client.post("/api/terminal/ticket", headers=H)
    assert r.status_code == 200 and r.json()["ticket"]


def test_terminal_refuse_a_un_autre_super_admin(client):
    connecter(client, "serveur-demo", "demo-mot-de-passe")
    c, mdp = accounts.create("second", "Second", "super", None, "test", "super", set())
    nouveau = "Un-mot-de-passe-solide-42"
    accounts.change_own_password("second", nouveau)
    connecter(client, "second", nouveau)
    r = client.post("/api/terminal/ticket", headers=H)
    assert r.status_code == 403
    assert "compte du serveur" in r.json()["detail"]


def test_dockge_jamais_ouvert_au_wifi(client):
    connecter(client, "serveur-demo", "demo-mot-de-passe")
    r = client.post("/api/invites", json={"action": "wifi", "outil": "dockge", "wifi": True}, headers=H)
    assert r.status_code == 403
    r = client.post("/api/invites", json={"action": "wifi", "outil": "jellyfin", "wifi": True}, headers=H)
    assert r.status_code == 200


def test_regles_pare_feu_bloquent_toujours_dockge():
    regles = guest_network.wanted_rules("enp1s0", {"wifi": {"dockge": True, "jellyfin": True}})
    assert any("--ctorigdstport 5001" in r for r in regles)
    assert not any("--ctorigdstport 8096" in r for r in regles)


def test_api_admin_sans_session(client):
    client.cookies.clear()
    assert client.get("/api/etat").status_code in (401, 403)


def test_choix_du_redemarrage(client):
    connecter(client, "serveur-demo", "demo-mot-de-passe")
    assert client.get("/api/systeme/redemarrage", headers=H).json()["mode"] in ("auto", "manuel")
    assert client.post("/api/systeme/redemarrage", json={"mode": "manuel"}, headers=H).status_code == 200
    assert client.get("/api/systeme/redemarrage", headers=H).json() == {"mode": "manuel", "choisi": True}
    assert client.post("/api/systeme/redemarrage", json={"mode": "reboot"}, headers=H).status_code == 400
    assert client.post("/api/systeme/redemarrage", json={"mode": "auto"}, headers=H).status_code == 200
    assert client.get("/api/systeme/redemarrage", headers=H).json()["mode"] == "auto"
    from services import activity_log

    assert any("Redémarrage après une mise à jour" in str(e) for e in activity_log.read(20))


def test_choix_du_redemarrage_reserve_au_super_admin(client):
    connecter(client, "serveur-demo", "demo-mot-de-passe")
    c, _ = accounts.create("simple", "Simple", "admin", None, "test", "admin", set())
    nouveau = "Un-mot-de-passe-solide-43"
    accounts.change_own_password("simple", nouveau)
    connecter(client, "simple", nouveau)
    assert client.get("/api/systeme/redemarrage", headers=H).status_code == 200
    assert client.post("/api/systeme/redemarrage", json={"mode": "manuel"}, headers=H).status_code == 403


def test_xml_de_nextcloud_lu_avec_le_lecteur_protege():
    """Une réponse XML piégée (entités en cascade) est refusée au lieu de saturer la mémoire."""
    import pytest
    from defusedxml import EntitiesForbidden

    from services import nextcloud_files

    piege = b"""<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>
<d:multistatus xmlns:d="DAV:"><d:response>&b;</d:response></d:multistatus>"""
    with pytest.raises(EntitiesForbidden):
        nextcloud_files.parse_xml(piege)
    normal = b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>/x</d:href></d:response></d:multistatus>'
    assert nextcloud_files.parse_xml(normal).find("d:response/d:href", nextcloud_files.DAV_NS).text == "/x"


def test_mauvais_mots_de_passe_ne_bloquent_que_leur_auteur(client, monkeypatch):
    from security import server_password

    monkeypatch.setattr(server_password, "_failures", {})
    client.cookies.clear()
    curieux = {**H, "X-Demo-Login": "curieux@exemple.fr"}
    for _ in range(server_password.MAX_ATTEMPTS):
        client.post(
            "/api/admin/connexion", json={"identifiant": "serveur-demo", "mot_de_passe": "faux"}, headers=curieux
        )
    r = client.post(
        "/api/admin/connexion", json={"identifiant": "serveur-demo", "mot_de_passe": "faux"}, headers=curieux
    )
    assert r.status_code == 429
    r = client.post(
        "/api/admin/connexion", json={"identifiant": "serveur-demo", "mot_de_passe": "demo-mot-de-passe"}, headers=H
    )
    assert r.status_code == 200  # l'admin, lui, se connecte normalement


def test_partages_api(client):
    connecter(client, "serveur-demo", "demo-mot-de-passe")
    r = client.get("/api/partages", headers=H)
    assert r.status_code == 200 and r.json()["partages"] == [] and set(r.json()["modes"]) == {"lecture", "ecriture"}
    r = client.post(
        "/api/partages", json={"chemin": "/R2-D2/Projets", "groupe": "membres", "mode": "lecture"}, headers=H
    )
    assert r.status_code == 409  # pas de Nextcloud dans la démo
    assert client.post("/api/partages/inconnu/arreter", headers=H).status_code == 400
