"""Option « Serveur ouvert au public » : liens Nextcloud protégés, RGPD, comptes inactifs, demandes de fichiers."""

import time

import pytest
from fastapi.testclient import TestClient

import main
from security import second_factor, totp
from services import public_mode

H = {"X-Jabba": "1"}


class FauxOcc:
    def __init__(self):
        self.config = {
            "core": {"shareapi_enforce_links_password": "no"},
            "registration": {"additional_hint": "Charte du lieu."},
        }

    def __call__(self, args, input_text=None, is_password=False, timeout=60):
        if args[0] == "config:app:get":
            value = self.config.get(args[1], {}).get(args[2])
            return (0, value + "\n", "") if value is not None else (1, "", "")
        if args[0] == "config:app:set":
            self.config.setdefault(args[1], {})[args[2]] = args[4]
            return 0, "", ""
        if args[0] == "config:app:delete":
            self.config.get(args[1], {}).pop(args[2], None)
            return 0, "", ""
        if args[0] == "status":
            return 1, "", ""
        return 1, "", ""


def test_liens_proteges_puis_reglages_d_avant_remis():
    occ = FauxOcc()
    reglages = dict(public_mode.DEFAULTS, expiration_jours=14)
    reglages = public_mode.apply_link_policy(occ, True, reglages)
    core = occ.config["core"]
    assert core["shareapi_enforce_links_password"] == "yes"
    assert core["shareapi_enforce_expire_date"] == "yes" and core["shareapi_expire_after_n_days"] == "14"
    public_mode.apply_link_policy(occ, True, reglages)  # deux fois : on garde les réglages d'origine
    public_mode.apply_link_policy(occ, False, reglages)
    assert occ.config["core"] == {"shareapi_enforce_links_password": "no"}
    assert reglages["avant"] == {}


def test_mentions_rgpd():
    texte = public_mode.privacy_notice(
        {"organisme": "Le Fablab", "contact": "bureau@fablab.exemple", "inactif_mois": 18}
    )
    for attendu in [
        "Le Fablab",
        "bureau@fablab.exemple",
        "18 mois",
        "CNIL",
        "télécharger tous tes fichiers",
        "Rien n'est vendu",
    ]:
        assert attendu in texte
    assert len(texte) < 1000  # tient dans la charte (1 500 caractères au plus)
    assert "l'association" in public_mode.privacy_notice({})


def test_comptes_inactifs():
    maintenant = time.mktime((2026, 11, 16, 12, 0, 0, 0, 0, 0))
    comptes = [
        {"id": "recent", "etat": "actif", "vu": "2026-10-01T10:00:00+00:00"},
        {"id": "ancien", "etat": "actif", "vu": "2025-06-01T10:00:00+00:00"},
        {"id": "admin", "etat": "actif", "admin": True, "vu": "2020-01-01T00:00:00+00:00"},
        {"id": "suspendu", "etat": "suspendu", "vu": "2020-01-01T00:00:00+00:00"},
        {"id": "jamais-vieux", "etat": "actif", "vu": None, "cree": "2024-01-01T00:00:00+00:00"},
        {"id": "jamais-neuf", "etat": "actif", "vu": None, "cree": "2026-11-01T00:00:00+00:00"},
        {"id": "illisible", "etat": "actif", "vu": "hier"},
    ]
    trouves = public_mode.inactive_accounts(comptes, 12, maintenant)
    assert [c["id"] for c in trouves] == ["ancien", "jamais-vieux"]
    assert trouves[0]["inactif_depuis"] > 500 and trouves[1]["inactif_depuis"] is None


def test_alerte_mensuelle(tmp_path, monkeypatch):
    monkeypatch.setattr(public_mode, "SETTINGS_FILE", str(tmp_path / "public.json"))
    alertes = []
    monkeypatch.setattr(public_mode.notifications, "send", lambda *a, **k: alertes.append(a))
    comptes = lambda occ: [{"id": "x", "etat": "actif", "vu": "2020-01-01T00:00:00+00:00"}]  # noqa: E731
    public_mode.watch_inactive_accounts(None, comptes, now=2_000_000_000)
    assert alertes == []  # mode public fermé : rien
    public_mode.write({**public_mode.read(), "actif": True})
    public_mode.watch_inactive_accounts(None, comptes, now=2_000_000_000)
    public_mode.watch_inactive_accounts(None, comptes, now=2_000_000_000 + 86400)
    assert len(alertes) == 1 and "1 compte inactif" == alertes[0][1]
    public_mode.watch_inactive_accounts(None, comptes, now=2_000_000_000 + 31 * 86400)
    assert len(alertes) == 2


def test_demande_de_fichiers_deposer_seulement():
    envoye = {}

    def ocs(methode, chemin, donnees):
        envoye.update(donnees)
        return {"id": 42, "url": "https://nuage.exemple/s/AbC"}

    r = public_mode.create_file_request(ocs, "/Photos/Mariage", 500, "secret-partage", "")
    assert envoye["permissions"] == 4 and envoye["shareType"] == 3 and envoye["password"] == "secret-partage"
    assert envoye["label"] == "Demande de fichiers" and r["url"].endswith("/s/AbC") and r["id"] == "42"
    import datetime

    assert r["expire"] == (datetime.date.today() + datetime.timedelta(days=90)).isoformat()  # 90 jours au plus


# ---------------------------------------------------------------- par l'API
@pytest.fixture
def client(monkeypatch):
    occ = FauxOcc()
    monkeypatch.setattr(public_mode, "SETTINGS_FILE", public_mode.SETTINGS_FILE + ".test")
    import routes.public_mode as routes_public
    from host import commands

    monkeypatch.setattr(commands, "occ", occ)
    monkeypatch.setattr(routes_public.commands, "occ", occ)
    second_factor.clear("serveur-demo")
    with TestClient(main.app, base_url="https://testserver") as c:
        c.cookies.clear()
        c.post(
            "/api/admin/connexion", json={"identifiant": "serveur-demo", "mot_de_passe": "demo-mot-de-passe"}, headers=H
        )
        c.occ = occ
        yield c
    second_factor.clear("serveur-demo")
    import os

    if os.path.exists(public_mode.SETTINGS_FILE):
        os.remove(public_mode.SETTINGS_FILE)


def test_ouvrir_demande_la_double_verification(client):
    r = client.post("/api/public/ouvrir", json={"actif": True}, headers=H)
    assert r.status_code == 400 and "double vérification" in r.json()["detail"]
    secret, _ = second_factor.start_app_enrolment("serveur-demo")
    second_factor.finish_app_enrolment("serveur-demo", totp.code_at(secret, time.time()))
    apercu = client.get("/api/public").json()
    assert {p["id"]: p["ok"] for p in apercu["pret"]}["double"] is True
    r = client.post("/api/public/ouvrir", json={"actif": True}, headers=H)
    assert r.status_code == 200 and r.json()["actif"] is True and "avant" not in r.json()
    assert client.occ.config["core"]["shareapi_enforce_links_password"] == "yes"
    client.post("/api/public/ouvrir", json={"actif": False}, headers=H)
    assert client.occ.config["core"]["shareapi_enforce_links_password"] == "no"


def test_reglages_bornes(client):
    r = client.post(
        "/api/public/reglages",
        json={"organisme": "  Le   Fablab  ", "contact": "x" * 500, "inactif_mois": 1, "expiration_jours": 9999},
        headers=H,
    )
    assert r.json()["organisme"] == "Le Fablab" and len(r.json()["contact"]) == 120
    assert r.json()["inactif_mois"] == 3 and r.json()["expiration_jours"] == 365


def test_mentions_ajoutees_une_seule_fois(client):
    client.post("/api/public/reglages", json={"organisme": "Le Fablab", "contact": "bureau@exemple"}, headers=H)
    client.post("/api/public/mentions", headers=H)
    client.post("/api/public/reglages", json={"contact": "contact@exemple"}, headers=H)
    client.post("/api/public/mentions", headers=H)
    charte = client.occ.config["registration"]["additional_hint"]
    assert charte.startswith("Charte du lieu. Données personnelles : Le Fablab")
    assert charte.count("Données personnelles") == 1 and "contact@exemple" in charte


def test_demande_de_fichiers_refusee_si_ferme(client):
    r = client.post("/api/demandes-fichiers", json={"chemin": "/Photos"}, headers=H)
    assert r.status_code == 400 and "ouvert au public" in r.json()["detail"]
