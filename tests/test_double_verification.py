"""Double vérification des admins : appli d'authentification, clé d'accès (Face ID), codes de secours."""

import time

import pytest
from cle_logicielle import CleLogicielle
from fastapi.testclient import TestClient

import main
from security import second_factor, server_password, totp

H = {"X-Jabba": "1"}
SERVEUR = ("serveur-demo", "demo-mot-de-passe")


# ---------------------------------------------------------------- TOTP (RFC 6238, annexe B)
SECRET_RFC = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # « 12345678901234567890 »


@pytest.mark.parametrize(
    "moment, code", [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")]
)
def test_codes_de_la_norme(moment, code):
    assert totp.code_at(SECRET_RFC, moment) == code


def test_code_accepte_avec_un_petit_decalage_mais_jamais_deux_fois():
    maintenant = 1_700_000_000
    code_suivant = totp.code_at(SECRET_RFC, maintenant + 30)
    creneau = totp.matching_step(SECRET_RFC, code_suivant, maintenant)
    assert creneau == maintenant // 30 + 1
    assert totp.matching_step(SECRET_RFC, code_suivant, maintenant, last_step=creneau) is None  # déjà servi
    assert totp.matching_step(SECRET_RFC, totp.code_at(SECRET_RFC, maintenant + 90), maintenant) is None
    assert totp.matching_step(SECRET_RFC, "12 34", maintenant) is None
    assert totp.matching_step(SECRET_RFC, None, maintenant) is None


def test_lien_pour_le_qr_code():
    lien = totp.provisioning_uri("ABC", "rob dupont")
    assert lien.startswith("otpauth://totp/Jabba%3Arob%20dupont?")
    assert "secret=ABC" in lien and "issuer=Jabba" in lien


# ---------------------------------------------------------------- parcours complet par l'API
@pytest.fixture
def client():
    second_factor.clear(SERVEUR[0])
    server_password._failures.clear()
    with TestClient(main.app, base_url="https://testserver") as c:
        yield c
    second_factor.clear(SERVEUR[0])
    server_password._failures.clear()


def connexion(client, ident=SERVEUR[0], mdp=SERVEUR[1]):
    client.cookies.clear()
    r = client.post("/api/admin/connexion", json={"identifiant": ident, "mot_de_passe": mdp}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def activer_appli(client):
    secret = client.post("/api/double/appli/debut", headers=H).json()["secret"]
    qr = client.get("/api/double/appli/qr.svg")
    assert qr.status_code == 200 and qr.text.lstrip().startswith("<?xml") or "<svg" in qr.text
    r = client.post("/api/double/appli/fin", json={"code": totp.code_at(secret, time.time())}, headers=H)
    assert r.status_code == 200, r.text
    return secret, r.json()["codes"]


def test_sans_double_verification_la_connexion_ne_change_pas(client):
    assert "etape" not in connexion(client)
    assert client.get("/api/double").json()["active"] is False


def test_appli_d_authentification(client):
    connexion(client)
    mauvais = client.post("/api/double/appli/debut", headers=H)
    assert mauvais.status_code == 200
    r = client.post("/api/double/appli/fin", json={"code": "000000"}, headers=H)
    assert r.status_code == 400 and "incorrect" in r.json()["detail"]
    secret, codes = activer_appli(client)
    assert len(codes) == 10 and len(set(codes)) == 10
    assert client.get("/api/double").json() == {"active": True, "appli": True, "cles": [], "codes_restants": 10}

    etape = connexion(client)
    assert etape["methodes"] == ["appli", "secours"] and "identifiant" not in etape
    assert client.get("/api/double").status_code == 401  # pas encore connecté

    faux = client.post(
        "/api/admin/verification", json={"etape": etape["etape"], "methode": "appli", "code": "1"}, headers=H
    )
    assert faux.status_code == 403
    bon = totp.code_at(secret, time.time() + 30)  # le créneau suivant (celui d'avant a servi à l'activation)
    r = client.post(
        "/api/admin/verification", json={"etape": etape["etape"], "methode": "appli", "code": bon}, headers=H
    )
    assert r.status_code == 200 and r.json()["identifiant"] == SERVEUR[0]
    assert client.get("/api/double").status_code == 200  # connecté

    # le même code ne resservira pas, et l'étape a disparu
    again = client.post(
        "/api/admin/verification", json={"etape": etape["etape"], "methode": "appli", "code": bon}, headers=H
    )
    assert again.status_code == 401


def test_codes_de_secours_a_usage_unique(client):
    connexion(client)
    _, codes = activer_appli(client)
    etape = connexion(client)["etape"]
    r = client.post(
        "/api/admin/verification",
        json={"etape": etape, "methode": "secours", "code": codes[3].upper().replace("-", " ")},
        headers=H,
    )
    assert r.status_code == 200
    assert client.get("/api/double").json()["codes_restants"] == 9
    etape = connexion(client)["etape"]
    r = client.post("/api/admin/verification", json={"etape": etape, "methode": "secours", "code": codes[3]}, headers=H)
    assert r.status_code == 403
    # nouveaux codes : les anciens ne marchent plus
    r = client.post("/api/admin/verification", json={"etape": etape, "methode": "secours", "code": codes[4]}, headers=H)
    nouveaux = client.post("/api/double/codes", headers=H).json()["codes"]
    assert not set(nouveaux) & set(codes)
    etape = connexion(client)["etape"]
    r = client.post("/api/admin/verification", json={"etape": etape, "methode": "secours", "code": codes[5]}, headers=H)
    assert r.status_code == 403


def test_trop_d_essais_ferme_l_etape(client):
    connexion(client)
    activer_appli(client)
    etape = connexion(client)["etape"]
    for _ in range(second_factor.LOGIN_STEP_ATTEMPTS):
        server_password._failures.clear()  # on teste l'étape, pas le blocage de 10 minutes
        client.post(
            "/api/admin/verification", json={"etape": etape, "methode": "secours", "code": "aaaa-aaaa"}, headers=H
        )
    r = client.post(
        "/api/admin/verification", json={"etape": etape, "methode": "secours", "code": "aaaa-aaaa"}, headers=H
    )
    assert r.status_code == 401 and "expirée" in r.json()["detail"]


def test_methode_inconnue_ou_etape_inventee(client):
    r = client.post("/api/admin/verification", json={"etape": "invente", "methode": "appli", "code": "1"}, headers=H)
    assert r.status_code == 401
    connexion(client)
    activer_appli(client)
    etape = connexion(client)["etape"]
    r = client.post("/api/admin/verification", json={"etape": etape, "methode": "sms", "code": "1"}, headers=H)
    assert r.status_code == 400


def test_cle_d_acces_face_id(client):
    connexion(client)
    cle = CleLogicielle("testserver")
    options = client.post("/api/double/cle/debut", headers=H).json()
    assert options["rp"]["id"] == "testserver" and options["authenticatorSelection"]["userVerification"] == "required"
    r = client.post(
        "/api/double/cle/fin",
        json={"credential": cle.creer(options, "https://testserver"), "nom": "iPhone de Rob"},
        headers=H,
    )
    assert r.status_code == 200 and len(r.json()["codes"]) == 10
    etat = client.get("/api/double").json()
    assert [k["nom"] for k in etat["cles"]] == ["iPhone de Rob"]

    etape = connexion(client)
    assert etape["methodes"] == ["cle", "secours"]
    options = client.post("/api/admin/cle/options", json={"etape": etape["etape"]}, headers=H).json()
    assert options["allowCredentials"][0]["id"] == etat["cles"][0]["id"]
    r = client.post(
        "/api/admin/verification",
        json={"etape": etape["etape"], "methode": "cle", "credential": cle.signer(options, "https://testserver")},
        headers=H,
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/double").json()["cles"][0]["utilisee"]

    # une signature pour un autre site, ou rejouée, est refusée
    etape = connexion(client)["etape"]
    options = client.post("/api/admin/cle/options", json={"etape": etape}, headers=H).json()
    r = client.post(
        "/api/admin/verification",
        json={
            "etape": etape,
            "methode": "cle",
            "credential": cle.signer(options, "https://pirate.exemple", "pirate.exemple"),
        },
        headers=H,
    )
    assert r.status_code == 403
    autre = CleLogicielle("testserver")
    options = client.post("/api/admin/cle/options", json={"etape": etape}, headers=H).json()
    r = client.post(
        "/api/admin/verification",
        json={"etape": etape, "methode": "cle", "credential": autre.signer(options, "https://testserver")},
        headers=H,
    )
    assert r.status_code == 403


def test_cle_mal_formee_refusee(client):
    connexion(client)
    client.post("/api/double/cle/debut", headers=H)
    r = client.post("/api/double/cle/fin", json={"credential": {"id": "x"}, "nom": "?"}, headers=H)
    assert r.status_code == 400
    r = client.post("/api/double/cle/fin", json={"credential": {"id": "x"}}, headers=H)
    assert r.status_code == 400 and "expiré" in r.json()["detail"]


def test_retirer_la_derniere_methode_efface_les_codes(client):
    connexion(client)
    activer_appli(client)
    client.post("/api/double/appli/retirer", headers=H)
    assert client.get("/api/double").json() == {"active": False, "appli": False, "cles": [], "codes_restants": 0}
    assert "etape" not in connexion(client)
    r = client.post("/api/double/codes", headers=H)
    assert r.status_code == 400


def test_reglages_reserves_a_un_admin_qui_vient_de_confirmer(client, monkeypatch):
    client.cookies.clear()
    assert client.post("/api/double/appli/debut", headers=H).status_code == 401
    connexion(client)
    from security import sessions

    monkeypatch.setattr(sessions, "confirmed_recently", lambda session: False)
    r = client.post("/api/double/appli/debut", headers=H)
    assert r.status_code == 401 and r.json()["detail"]["confirmer"] is True
    assert client.get("/api/double").status_code == 200  # la simple lecture reste possible


def test_super_admin_retire_la_double_verification_d_un_autre(client):
    from security import accounts

    connexion(client)
    compte, mdp = accounts.create("perdu", "Perdu", "admin", None, "test", "super", set())
    accounts.change_own_password("perdu", "Un-mot-de-passe-solide-77")
    connexion(client, "perdu", "Un-mot-de-passe-solide-77")
    activer_appli(client)
    assert "etape" in connexion(client, "perdu", "Un-mot-de-passe-solide-77")

    connexion(client)
    assert client.post("/api/double/effacer", json={"identifiant": SERVEUR[0]}, headers=H).status_code == 400
    assert client.post("/api/double/effacer", json={"identifiant": "perdu"}, headers=H).status_code == 200
    assert "etape" not in connexion(client, "perdu", "Un-mot-de-passe-solide-77")
    # un simple admin ne peut pas le faire
    assert client.post("/api/double/effacer", json={"identifiant": SERVEUR[0]}, headers=H).status_code == 403
    connexion(client)
    client.post(f"/api/comptes/{compte['id']}", json={"action": "supprimer"}, headers=H)
