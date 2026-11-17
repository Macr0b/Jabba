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


def connecter(client, ident, mdp):
    client.cookies.clear()
    r = client.post("/api/admin/connexion", json={"identifiant": ident, "mot_de_passe": mdp}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def test_le_terminal_n_existe_plus(client):
    """Retiré en 0.14 : un shell complet ne rentre pas dans la liste fermée des actions du serveur."""
    connecter(client, "serveur-demo", "demo-mot-de-passe")
    assert client.post("/api/terminal/ticket", headers=H).status_code in (404, 405)


def test_dockge_jamais_ouvert_au_wifi(client):
    connecter(client, "serveur-demo", "demo-mot-de-passe")
    r = client.post("/api/invites", json={"action": "wifi", "outil": "dockge", "wifi": True}, headers=H)
    assert r.status_code == 403
    r = client.post("/api/invites", json={"action": "wifi", "outil": "jellyfin", "wifi": True}, headers=H)
    assert r.status_code == 200


def test_regles_pare_feu_bloquent_toujours_dockge():
    ports = guest_network.closed_ports({"wifi": {"dockge": True, "jellyfin": True}})
    assert 5001 in ports  # Dockge n'est jamais ouvert au Wi-Fi, même coché
    assert 8096 not in ports


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


def test_interface_reverifiee_a_chaque_ouverture(client):
    """Après une mise à jour, le navigateur ne garde pas d'anciens modules JS : il redemande (304 si inchangé)."""
    r = client.get("/static/js/main.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    etag = r.headers["etag"]
    assert client.get("/static/js/main.js", headers={"If-None-Match": etag}).status_code == 304


def test_adresses_de_l_interface_changent_a_chaque_version(client):
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


def test_antivirus_disques_par_l_api(client, tmp_path, monkeypatch):
    """La carte de l'Établi : les disques, cocher pour la semaine, analyser maintenant (démo)."""
    from services import antivirus

    monkeypatch.setattr(antivirus, "STATE_FILE", str(tmp_path / "av.json"))
    connecter(client, "serveur-demo", "demo-mot-de-passe")
    disques = client.get("/api/antivirus", headers=H).json()["disques"]
    assert disques[0] == {"chemin": "/srv/data", "nom": "Disque de données", "chaque_semaine": True, "analyse": None}
    r = client.post("/api/antivirus/disques", json={"chemin": "/srv/data", "chaque_semaine": False}, headers=H)
    assert r.status_code == 200
    assert client.get("/api/antivirus", headers=H).json()["disques"][0]["chaque_semaine"] is False
    assert client.post("/api/antivirus/analyser", json={"chemin": "/etc"}, headers=H).status_code == 404
    assert client.post("/api/antivirus/analyser", json={"chemin": "/srv/data"}, headers=H).status_code == 200
