"""Double vérification des admins : appli d'authentification, clé d'accès (Face ID), codes de secours."""

import time

import pytest
from fastapi.testclient import TestClient
from software_key import SoftwareKey

import main
from security import second_factor, server_password, totp

H = {"X-Jabba": "1"}
SERVER = ("serveur-demo", "demo-mot-de-passe")


# ---------------------------------------------------------------- TOTP (RFC 6238, annexe B)
SECRET_RFC = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # « 12345678901234567890 »


@pytest.mark.parametrize(
    "moment, code", [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")]
)
def test_codes_from_the_standard(moment, code):
    assert totp.code_at(SECRET_RFC, moment) == code


def test_code_accepted_with_small_drift_but_never_twice():
    right_now = 1_700_000_000
    next_code = totp.code_at(SECRET_RFC, right_now + 30)
    slot = totp.matching_step(SECRET_RFC, next_code, right_now)
    assert slot == right_now // 30 + 1
    assert totp.matching_step(SECRET_RFC, next_code, right_now, last_step=slot) is None  # déjà servi
    assert totp.matching_step(SECRET_RFC, totp.code_at(SECRET_RFC, right_now + 90), right_now) is None
    assert totp.matching_step(SECRET_RFC, "12 34", right_now) is None
    assert totp.matching_step(SECRET_RFC, None, right_now) is None


def test_qr_code_link():
    link = totp.provisioning_uri("ABC", "rob dupont")
    assert link.startswith("otpauth://totp/Jabba%3Arob%20dupont?")
    assert "secret=ABC" in link and "issuer=Jabba" in link


# ---------------------------------------------------------------- parcours complet par l'API
@pytest.fixture
def client():
    second_factor.clear(SERVER[0])
    server_password._failures.clear()
    with TestClient(main.app, base_url="https://testserver") as c:
        yield c
    second_factor.clear(SERVER[0])
    server_password._failures.clear()


def login(client, identifier=SERVER[0], password=SERVER[1]):
    client.cookies.clear()
    r = client.post("/api/admin/connexion", json={"identifiant": identifier, "mot_de_passe": password}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def enable_app(client):
    secret = client.post("/api/double/appli/debut", headers=H).json()["secret"]
    qr = client.get("/api/double/appli/qr.svg")
    assert qr.status_code == 200 and qr.text.lstrip().startswith("<?xml") or "<svg" in qr.text
    r = client.post("/api/double/appli/fin", json={"code": totp.code_at(secret, time.time())}, headers=H)
    assert r.status_code == 200, r.text
    return secret, r.json()["codes"]


def test_without_two_factor_login_is_unchanged(client):
    assert "etape" not in login(client)
    assert client.get("/api/double").json()["active"] is False


def test_authenticator_app(client):
    login(client)
    bad = client.post("/api/double/appli/debut", headers=H)
    assert bad.status_code == 200
    r = client.post("/api/double/appli/fin", json={"code": "000000"}, headers=H)
    assert r.status_code == 400 and "incorrect" in r.json()["detail"]
    secret, codes = enable_app(client)
    assert len(codes) == 10 and len(set(codes)) == 10
    assert client.get("/api/double").json() == {"active": True, "appli": True, "cles": [], "codes_restants": 10}

    step = login(client)
    assert step["methodes"] == ["appli", "secours"] and "identifiant" not in step
    assert client.get("/api/double").status_code == 401  # pas encore connecté

    fake = client.post(
        "/api/admin/verification", json={"etape": step["etape"], "methode": "appli", "code": "1"}, headers=H
    )
    assert fake.status_code == 403
    good = totp.code_at(secret, time.time() + 30)  # le créneau suivant (celui d'avant a servi à l'activation)
    r = client.post(
        "/api/admin/verification", json={"etape": step["etape"], "methode": "appli", "code": good}, headers=H
    )
    assert r.status_code == 200 and r.json()["identifiant"] == SERVER[0]
    assert client.get("/api/double").status_code == 200  # connecté

    # le même code ne resservira pas, et l'étape a disparu
    again = client.post(
        "/api/admin/verification", json={"etape": step["etape"], "methode": "appli", "code": good}, headers=H
    )
    assert again.status_code == 401


def test_single_use_recovery_codes(client):
    login(client)
    _, codes = enable_app(client)
    step = login(client)["etape"]
    r = client.post(
        "/api/admin/verification",
        json={"etape": step, "methode": "secours", "code": codes[3].upper().replace("-", " ")},
        headers=H,
    )
    assert r.status_code == 200
    assert client.get("/api/double").json()["codes_restants"] == 9
    step = login(client)["etape"]
    r = client.post("/api/admin/verification", json={"etape": step, "methode": "secours", "code": codes[3]}, headers=H)
    assert r.status_code == 403
    # nouveaux codes : les anciens ne marchent plus
    r = client.post("/api/admin/verification", json={"etape": step, "methode": "secours", "code": codes[4]}, headers=H)
    new_ones = client.post("/api/double/codes", headers=H).json()["codes"]
    assert not set(new_ones) & set(codes)
    step = login(client)["etape"]
    r = client.post("/api/admin/verification", json={"etape": step, "methode": "secours", "code": codes[5]}, headers=H)
    assert r.status_code == 403


def test_too_many_tries_close_the_step(client):
    login(client)
    enable_app(client)
    step = login(client)["etape"]
    for _ in range(second_factor.LOGIN_STEP_ATTEMPTS):
        server_password._failures.clear()  # on teste l'étape, pas le blocage de 10 minutes
        client.post(
            "/api/admin/verification", json={"etape": step, "methode": "secours", "code": "aaaa-aaaa"}, headers=H
        )
    r = client.post(
        "/api/admin/verification", json={"etape": step, "methode": "secours", "code": "aaaa-aaaa"}, headers=H
    )
    assert r.status_code == 401 and "expirée" in r.json()["detail"]


def test_unknown_method_or_invented_step(client):
    r = client.post("/api/admin/verification", json={"etape": "invente", "methode": "appli", "code": "1"}, headers=H)
    assert r.status_code == 401
    login(client)
    enable_app(client)
    step = login(client)["etape"]
    r = client.post("/api/admin/verification", json={"etape": step, "methode": "sms", "code": "1"}, headers=H)
    assert r.status_code == 400


def test_passkey_face_id(client):
    login(client)
    key = SoftwareKey("testserver")
    options = client.post("/api/double/cle/debut", headers=H).json()
    assert options["rp"]["id"] == "testserver" and options["authenticatorSelection"]["userVerification"] == "required"
    r = client.post(
        "/api/double/cle/fin",
        json={"credential": key.create_item(options, "https://testserver"), "nom": "iPhone de Rob"},
        headers=H,
    )
    assert r.status_code == 200 and len(r.json()["codes"]) == 10
    etat = client.get("/api/double").json()
    assert [k["nom"] for k in etat["cles"]] == ["iPhone de Rob"]

    step = login(client)
    assert step["methodes"] == ["cle", "secours"]
    options = client.post("/api/admin/cle/options", json={"etape": step["etape"]}, headers=H).json()
    assert options["allowCredentials"][0]["id"] == etat["cles"][0]["id"]
    r = client.post(
        "/api/admin/verification",
        json={"etape": step["etape"], "methode": "cle", "credential": key.sign_with(options, "https://testserver")},
        headers=H,
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/double").json()["cles"][0]["utilisee"]

    # une signature pour un autre site, ou rejouée, est refusée
    step = login(client)["etape"]
    options = client.post("/api/admin/cle/options", json={"etape": step}, headers=H).json()
    r = client.post(
        "/api/admin/verification",
        json={
            "etape": step,
            "methode": "cle",
            "credential": key.sign_with(options, "https://pirate.exemple", "pirate.exemple"),
        },
        headers=H,
    )
    assert r.status_code == 403
    other = SoftwareKey("testserver")
    options = client.post("/api/admin/cle/options", json={"etape": step}, headers=H).json()
    r = client.post(
        "/api/admin/verification",
        json={"etape": step, "methode": "cle", "credential": other.sign_with(options, "https://testserver")},
        headers=H,
    )
    assert r.status_code == 403


def test_malformed_key_refused(client):
    login(client)
    client.post("/api/double/cle/debut", headers=H)
    r = client.post("/api/double/cle/fin", json={"credential": {"id": "x"}, "nom": "?"}, headers=H)
    assert r.status_code == 400
    r = client.post("/api/double/cle/fin", json={"credential": {"id": "x"}}, headers=H)
    assert r.status_code == 400 and "expiré" in r.json()["detail"]


def test_removing_the_last_method_erases_the_codes(client):
    login(client)
    enable_app(client)
    client.post("/api/double/appli/retirer", headers=H)
    assert client.get("/api/double").json() == {"active": False, "appli": False, "cles": [], "codes_restants": 0}
    assert "etape" not in login(client)
    r = client.post("/api/double/codes", headers=H)
    assert r.status_code == 400


def test_settings_only_for_an_admin_who_just_confirmed(client, monkeypatch):
    client.cookies.clear()
    assert client.post("/api/double/appli/debut", headers=H).status_code == 401
    login(client)
    from security import sessions

    monkeypatch.setattr(sessions, "confirmed_recently", lambda session: False)
    r = client.post("/api/double/appli/debut", headers=H)
    assert r.status_code == 401 and r.json()["detail"]["confirmer"] is True
    assert client.get("/api/double").status_code == 200  # la simple lecture reste possible


def test_super_admin_removes_someone_elses_two_factor(client):
    from security import accounts

    login(client)
    account, password = accounts.create("perdu", "Perdu", "admin", None, "test", "super", set())
    accounts.change_own_password("perdu", "Un-mot-de-passe-solide-77")
    login(client, "perdu", "Un-mot-de-passe-solide-77")
    enable_app(client)
    assert "etape" in login(client, "perdu", "Un-mot-de-passe-solide-77")

    login(client)
    assert client.post("/api/double/effacer", json={"identifiant": SERVER[0]}, headers=H).status_code == 400
    assert client.post("/api/double/effacer", json={"identifiant": "perdu"}, headers=H).status_code == 200
    assert "etape" not in login(client, "perdu", "Un-mot-de-passe-solide-77")
    # un simple admin ne peut pas le faire
    assert client.post("/api/double/effacer", json={"identifiant": SERVER[0]}, headers=H).status_code == 403
    login(client)
    client.post(f"/api/comptes/{account['id']}", json={"action": "supprimer"}, headers=H)
