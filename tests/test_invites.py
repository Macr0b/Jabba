"""La porte du Wi-Fi (page invités) : séparée de l'appli et de ses données (revue, point 1)."""
import json
import os

import pytest
import yaml
from fastapi.testclient import TestClient

import invites
import journal
from conftest import RACINE

H = {"X-Jabba": "1"}


@pytest.fixture
def etat(tmp_path, monkeypatch):
    f = tmp_path / "porte-wifi" / "etat.json"
    monkeypatch.setattr(invites, "FICHIER", str(f))
    monkeypatch.setattr(invites, "ANCIEN_FICHIER", str(tmp_path / "invites.json"))
    partage = tmp_path / "partage"
    partage.mkdir()
    (partage / "menu.txt").write_text("menu")
    invites.preparer_etat()
    invites.modifier(lambda d: d.update(actif=True, code="1234", serveur="holo",
                                        dossiers=[{"id": "p", "nom": "Partage", "chemin": str(partage), "mode": "depot"}]))
    return f, partage


def wifi(ip="192.168.1.20"):
    """La porte du Wi-Fi, vue depuis l'adresse `ip`."""
    async def depuis(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope, client=(ip, 5000))
        await invites.app(scope, receive, send)
    return TestClient(depuis)


def test_etat_cree_une_fois_et_garde(etat):
    f, _ = etat
    d = json.loads(f.read_text())
    assert d["code"] == "1234" and len(d["secret"]) == 64
    assert oct(os.stat(f).st_mode & 0o777) == "0o600"
    assert oct(os.stat(f.parent).st_mode & 0o777) == "0o700"


def test_reprise_de_l_ancien_emplacement(tmp_path, monkeypatch):
    ancien = tmp_path / "invites.json"
    ancien.write_text(json.dumps({"code": "9876", "secret": "s" * 64}))
    monkeypatch.setattr(invites, "FICHIER", str(tmp_path / "porte-wifi" / "etat.json"))
    monkeypatch.setattr(invites, "ANCIEN_FICHIER", str(ancien))
    invites.preparer_etat()
    assert invites.lire()["code"] == "9876" and not ancien.exists()


def test_parcours_invite(etat):
    c = wifi()
    assert c.get("/api/moi").json()["serveur"] == "holo"
    assert c.post("/api/invite/entrer", json={"code": "0000"}, headers=H).status_code == 403
    assert c.post("/api/invite/entrer", json={"code": "1234"}, headers=H).status_code == 200
    r = c.get("/api/invite/fichiers", params={"d": "p", "chemin": "/"})
    assert [e["nom"] for e in r.json()["elements"]] == ["menu.txt"]
    r = c.post("/api/invite/envoyer", params={"d": "p"}, files={"fichiers": ("../../evil.txt", b"x")}, headers=H)
    assert r.json()["envoyes"] == ["evil.txt"]


def test_pas_de_sortie_du_dossier(etat):
    c = wifi()
    c.post("/api/invite/entrer", json={"code": "1234"}, headers=H)
    for chemin in ("/../..", "/..", "/.cache", "/%2e%2e"):
        assert c.get("/api/invite/fichiers", params={"d": "p", "chemin": chemin}).status_code in (400, 404)


def test_administration_impossible(etat):
    c = wifi()
    assert c.get("/api/etat").status_code == 404
    assert c.post("/api/admin/connexion", json={}, headers=H).status_code == 404


def test_hors_reseau_local_refuse(etat):
    assert wifi("8.8.8.8").get("/api/invite/etat").status_code == 403


def test_trop_d_essais(etat):
    c = wifi("192.168.1.99")
    for _ in range(8):
        c.post("/api/invite/entrer", json={"code": "0000"}, headers=H)
    assert c.post("/api/invite/entrer", json={"code": "1234"}, headers=H).status_code == 429


def test_journal_de_la_porte_ne_peut_pas_se_faire_passer_pour_l_appli(tmp_path, monkeypatch):
    principal = tmp_path / "journal.jsonl"
    autre = tmp_path / "journal-invites.jsonl"
    monkeypatch.setattr(journal, "FICHIER", str(principal))
    monkeypatch.setattr(journal, "AUTRES", [str(autre)])
    journal.noter("admin", "vrai événement", "Rob")
    autre.write_text(json.dumps({"t": 1, "famille": "admin", "texte": "Faux : mot de passe changé", "qui": "Rob"}) + "\n"
                     + "pas du json\n")
    evs = journal.lire()
    faux = [e for e in evs if e["texte"].startswith("Faux")][0]
    assert faux["famille"] == "invite" and "qui" not in faux
    assert evs[0]["texte"] == "vrai événement"


def test_conteneur_de_la_porte_sans_pouvoirs():
    compose = yaml.safe_load(open(os.path.join(RACINE, "compose.yaml")))
    s = compose["services"]["jabba-invites"]
    assert s.get("privileged") is not True and "pid" not in s and s.get("network_mode") != "host"
    assert s["cap_drop"] == ["ALL"] and "cap_add" not in s
    assert s["read_only"] is True and "no-new-privileges:true" in s["security_opt"]
    assert not str(s["user"]).startswith("0")
    montages = [v if isinstance(v, str) else f"{v['source']}:{v['target']}" for v in s["volumes"]]
    assert "./donnees/porte-wifi:/etat" in montages
    assert not any(m.startswith("./donnees:") for m in montages), "la porte ne doit pas voir les données de l'appli"
    assert "invites:app" in " ".join(s["command"])
    app = compose["services"]["jabba-app"]
    assert "invites" not in " ".join(app.get("command", []) or [])
    dockerfile = open(os.path.join(RACINE, "Dockerfile")).read()
    assert "invites:app" not in dockerfile.split("CMD", 1)[1]


def test_etat_hors_des_dossiers_des_invites():
    """L'état (code, secret) n'est jamais dans un dossier que les invités voient."""
    import importlib
    import sys
    env = {"JABBA_DATA": "/data", "JABBA_DOSSIER_INVITES": "/srv/data/invites"}
    sauve = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    os.environ.pop("JABBA_INVITES_ETAT", None)
    try:
        m = importlib.reload(sys.modules["invites"])
        assert not os.path.commonpath([m.FICHIER, "/srv"]) == "/srv"
        assert m.FICHIER == "/data/porte-wifi/etat.json"
    finally:
        for k, v in sauve.items():
            os.environ[k] = v
        importlib.reload(sys.modules["invites"])
