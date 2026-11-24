"""Option « Serveur ouvert au public » : liens Nextcloud protégés, RGPD, comptes inactifs, demandes de fichiers."""

import time

import pytest
from fastapi.testclient import TestClient

import main
from security import second_factor, totp
from services import public_mode

H = {"X-Jabba": "1"}


class FakeOcc:
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


def test_links_protected_then_previous_settings_restored():
    occ = FakeOcc()
    settings = dict(public_mode.DEFAULTS, expiration_jours=14)
    settings = public_mode.apply_link_policy(occ, True, settings)
    core = occ.config["core"]
    assert core["shareapi_enforce_links_password"] == "yes"
    assert core["shareapi_enforce_expire_date"] == "yes" and core["shareapi_expire_after_n_days"] == "14"
    public_mode.apply_link_policy(occ, True, settings)  # deux fois : on garde les réglages d'origine
    public_mode.apply_link_policy(occ, False, settings)
    assert occ.config["core"] == {"shareapi_enforce_links_password": "no"}
    assert settings["avant"] == {}


def test_gdpr_notices():
    texte = public_mode.privacy_notice(
        {"organisme": "Le Fablab", "contact": "bureau@fablab.exemple", "inactif_mois": 18}
    )
    for expected in [
        "Le Fablab",
        "bureau@fablab.exemple",
        "18 mois",
        "CNIL",
        "télécharger tous tes fichiers",
        "Rien n'est vendu",
    ]:
        assert expected in texte
    assert len(texte) < 1000  # tient dans la charte (1 500 caractères au plus)
    assert "l'association" in public_mode.privacy_notice({})


def test_inactive_accounts():
    right_now = time.mktime((2026, 11, 16, 12, 0, 0, 0, 0, 0))
    accounts = [
        {"id": "recent", "etat": "actif", "vu": "2026-10-01T10:00:00+00:00"},
        {"id": "ancien", "etat": "actif", "vu": "2025-06-01T10:00:00+00:00"},
        {"id": "admin", "etat": "actif", "admin": True, "vu": "2020-01-01T00:00:00+00:00"},
        {"id": "suspendu", "etat": "suspendu", "vu": "2020-01-01T00:00:00+00:00"},
        {"id": "jamais-vieux", "etat": "actif", "vu": None, "cree": "2024-01-01T00:00:00+00:00"},
        {"id": "jamais-neuf", "etat": "actif", "vu": None, "cree": "2026-11-01T00:00:00+00:00"},
        {"id": "illisible", "etat": "actif", "vu": "hier"},
    ]
    found_items = public_mode.inactive_accounts(accounts, 12, right_now)
    assert [c["id"] for c in found_items] == ["ancien", "jamais-vieux"]
    assert found_items[0]["inactif_depuis"] > 500 and found_items[1]["inactif_depuis"] is None


def test_monthly_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(public_mode, "SETTINGS_FILE", str(tmp_path / "public.json"))
    alerts = []
    monkeypatch.setattr(public_mode.notifications, "send", lambda *a, **k: alerts.append(a))
    accounts = lambda occ: [{"id": "x", "etat": "actif", "vu": "2020-01-01T00:00:00+00:00"}]  # noqa: E731
    public_mode.watch_inactive_accounts(None, accounts, now=2_000_000_000)
    assert alerts == []  # mode public fermé : rien
    public_mode.write({**public_mode.read(), "actif": True})
    public_mode.watch_inactive_accounts(None, accounts, now=2_000_000_000)
    public_mode.watch_inactive_accounts(None, accounts, now=2_000_000_000 + 86400)
    assert len(alerts) == 1 and "1 compte inactif" == alerts[0][1]
    public_mode.watch_inactive_accounts(None, accounts, now=2_000_000_000 + 31 * 86400)
    assert len(alerts) == 2


def test_file_request_drop_only():
    sent_item = {}

    def ocs(method, chemin, donnees):
        sent_item.update(donnees)
        return {"id": 42, "url": "https://nuage.exemple/s/AbC"}

    r = public_mode.create_file_request(ocs, "/Photos/Mariage", 500, "secret-partage", "")
    assert sent_item["permissions"] == 4 and sent_item["shareType"] == 3 and sent_item["password"] == "secret-partage"
    assert sent_item["label"] == "Demande de fichiers" and r["url"].endswith("/s/AbC") and r["id"] == "42"
    import datetime

    assert r["expire"] == (datetime.date.today() + datetime.timedelta(days=90)).isoformat()  # 90 jours au plus


# ---------------------------------------------------------------- par l'API
@pytest.fixture
def client(monkeypatch):
    occ = FakeOcc()
    monkeypatch.setattr(public_mode, "SETTINGS_FILE", public_mode.SETTINGS_FILE + ".test")
    import routes.public_mode as public_routes
    from host import commands

    monkeypatch.setattr(commands, "occ", occ)
    monkeypatch.setattr(public_routes.commands, "occ", occ)
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


def test_opening_requires_two_factor(client):
    r = client.post("/api/public/ouvrir", json={"actif": True}, headers=H)
    assert r.status_code == 400 and "double vérification" in r.json()["detail"]
    secret, _ = second_factor.start_app_enrolment("serveur-demo")
    second_factor.finish_app_enrolment("serveur-demo", totp.code_at(secret, time.time()))
    preview = client.get("/api/public").json()
    assert {p["id"]: p["ok"] for p in preview["pret"]}["double"] is True
    r = client.post("/api/public/ouvrir", json={"actif": True}, headers=H)
    assert r.status_code == 200 and r.json()["actif"] is True and "avant" not in r.json()
    assert client.occ.config["core"]["shareapi_enforce_links_password"] == "yes"
    client.post("/api/public/ouvrir", json={"actif": False}, headers=H)
    assert client.occ.config["core"]["shareapi_enforce_links_password"] == "no"


def test_bounded_settings(client):
    r = client.post(
        "/api/public/reglages",
        json={"organisme": "  Le   Fablab  ", "contact": "x" * 500, "inactif_mois": 1, "expiration_jours": 9999},
        headers=H,
    )
    assert r.json()["organisme"] == "Le Fablab" and len(r.json()["contact"]) == 120
    assert r.json()["inactif_mois"] == 3 and r.json()["expiration_jours"] == 365


def test_notices_added_only_once(client):
    client.post("/api/public/reglages", json={"organisme": "Le Fablab", "contact": "bureau@exemple"}, headers=H)
    client.post("/api/public/mentions", headers=H)
    client.post("/api/public/reglages", json={"contact": "contact@exemple"}, headers=H)
    client.post("/api/public/mentions", headers=H)
    charter = client.occ.config["registration"]["additional_hint"]
    assert charter.startswith("Charte du lieu. Données personnelles : Le Fablab")
    assert charter.count("Données personnelles") == 1 and "contact@exemple" in charter


def test_file_request_refused_when_closed(client):
    r = client.post("/api/demandes-fichiers", json={"chemin": "/Photos"}, headers=H)
    assert r.status_code == 400 and "ouvert au public" in r.json()["detail"]
