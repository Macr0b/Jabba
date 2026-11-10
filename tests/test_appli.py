"""L'appli en démo : terminal réservé au compte du serveur (point 2), Dockge jamais ouvert au Wi-Fi (point 3)."""
import pytest
from fastapi.testclient import TestClient

import comptes
import main

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
    c, mdp = comptes.creer("second", "Second", "super", None, "test", "super", set())
    nouveau = "Un-mot-de-passe-solide-42"
    comptes.changer_mien("second", nouveau)
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
    regles = main.regles_voulues("enp1s0", {"wifi": {"dockge": True, "jellyfin": True}})
    assert any("--ctorigdstport 5001" in r for r in regles)
    assert not any("--ctorigdstport 8096" in r for r in regles)


def test_api_admin_sans_session(client):
    client.cookies.clear()
    assert client.get("/api/etat").status_code in (401, 403)
