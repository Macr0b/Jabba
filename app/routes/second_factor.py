"""API de la double vérification : la régler (chacun pour soi), puis s'en servir à la connexion.

Sommaire :
  GET  /api/double                      my_second_factor
  POST /api/double/appli/debut          start_app (QR code à scanner)
  GET  /api/double/appli/qr.svg         app_qr_code
  POST /api/double/appli/fin            finish_app (code tapé)
  POST /api/double/appli/retirer        remove_app
  POST /api/double/cle/debut            start_key (Face ID, Touch ID…)
  POST /api/double/cle/fin              finish_key
  POST /api/double/cle/{kid}/retirer    remove_key
  POST /api/double/codes                new_recovery_codes
  POST /api/double/effacer              clear_for_account (super admin : téléphone perdu)
  POST /api/admin/cle/options           key_login_options (connexion, étape 2)
  POST /api/admin/verification          verify_second_step (connexion, étape 2)
"""

import io

import segno
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from routes import admin_session
from security import accounts, guard, passkeys, second_factor, server_password
from services import activity_log, notifications

router = APIRouter()
_qr_links = {}  # identifiant -> lien otpauth en cours (montré une fois en QR code)
METHOD_NAMES = {"cle": "clé d'accès", "appli": "appli d'authentification", "secours": "code de secours"}


def _translate(function):
    try:
        return function()
    except second_factor.SecondFactorError as error:
        raise HTTPException(error.code, error.message)


def _relying_party(request):
    return passkeys.relying_party(request.headers.get("host", ""))


def _log(request, text):
    activity_log.record("admin", text, request.state.nom)


# ---------------------------------------------------------------- réglage (connecté)
@router.get("/api/double")
def my_second_factor(request: Request):
    session = guard.require_admin(request)
    return second_factor.summary(session["identifiant"])


@router.post("/api/double/appli/debut")
def start_app(request: Request):
    session = guard.require_admin(request, strong=True)
    secret, link = second_factor.start_app_enrolment(session["identifiant"])
    _qr_links[session["identifiant"]] = link
    return {"secret": secret}


@router.get("/api/double/appli/qr.svg")
def app_qr_code(request: Request):
    session = guard.require_admin(request, strong=True)
    link = _qr_links.get(session["identifiant"])
    if not link:
        raise HTTPException(404, "Aucun QR code en cours.")
    buffer = io.BytesIO()
    segno.make(link, error="m").save(buffer, kind="svg", scale=6, border=2, dark="#111418", light="#ffffff")
    return Response(buffer.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.post("/api/double/appli/fin")
async def finish_app(request: Request):
    session = guard.require_admin(request, strong=True)
    code = (await request.json()).get("code", "")
    codes = _translate(lambda: second_factor.finish_app_enrolment(session["identifiant"], code))
    _qr_links.pop(session["identifiant"], None)
    _log(request, f"Double vérification : appli d'authentification ajoutée ({session['identifiant']})")
    return {"ok": True, "codes": codes}


@router.post("/api/double/appli/retirer")
def remove_app(request: Request):
    session = guard.require_admin(request, strong=True)
    second_factor.remove_app(session["identifiant"])
    _log(request, f"Double vérification : appli d'authentification retirée ({session['identifiant']})")
    return second_factor.summary(session["identifiant"])


@router.post("/api/double/cle/debut")
def start_key(request: Request):
    session = guard.require_admin(request, strong=True)
    rp_id, _ = _relying_party(request)
    account = accounts.find(session["identifiant"]) or {}
    display_name = account.get("nom") or session["identifiant"]
    return _translate(lambda: second_factor.start_key_enrolment(session["identifiant"], display_name, rp_id))


@router.post("/api/double/cle/fin")
async def finish_key(request: Request):
    session = guard.require_admin(request, strong=True)
    body = await request.json()
    rp_id, origins = _relying_party(request)
    codes = _translate(
        lambda: second_factor.finish_key_enrolment(
            session["identifiant"], body.get("credential"), body.get("nom", ""), rp_id, origins
        )
    )
    _log(request, f"Double vérification : clé d'accès ajoutée ({session['identifiant']})")
    return {"ok": True, "codes": codes}


@router.post("/api/double/cle/{kid}/retirer")
def remove_key(kid: str, request: Request):
    session = guard.require_admin(request, strong=True)
    _translate(lambda: second_factor.remove_key(session["identifiant"], kid))
    _log(request, f"Double vérification : clé d'accès retirée ({session['identifiant']})")
    return second_factor.summary(session["identifiant"])


@router.post("/api/double/codes")
def new_recovery_codes(request: Request):
    session = guard.require_admin(request, strong=True)
    codes = _translate(lambda: second_factor.new_recovery_codes(session["identifiant"]))
    _log(request, f"Double vérification : nouveaux codes de secours ({session['identifiant']})")
    return {"codes": codes}


@router.post("/api/double/effacer")
async def clear_for_account(request: Request):
    """Un super admin retire la double vérification d'un autre compte (téléphone perdu)."""
    session = guard.require_super_admin(request, strong=True)
    identifier = ((await request.json()).get("identifiant") or "").strip().lower()
    if not identifier or identifier == session["identifiant"]:
        raise HTTPException(400, "Choisis un autre compte que le tien.")
    second_factor.clear(identifier)
    _log(request, f"Double vérification retirée du compte {identifier}, par {session['identifiant']}")
    notifications.send(
        "securite",
        "Double vérification retirée",
        f"Compte {identifier}, par {session['identifiant']}.",
        "/#/admins",
        family="admin",
        recipients=guard.super_admin_logins(),
    )
    return {"ok": True}


# ---------------------------------------------------------------- connexion, étape 2
@router.post("/api/admin/cle/options")
async def key_login_options(request: Request):
    token = (await request.json()).get("etape", "")
    rp_id, _ = _relying_party(request)
    return _translate(lambda: second_factor.key_login_options(token, rp_id))


@router.post("/api/admin/verification")
async def verify_second_step(request: Request):
    if server_password.is_locked():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    body = await request.json()
    method = body.get("methode", "")
    answer = body.get("credential") if method == "cle" else body.get("code", "")
    rp_id, origins = _relying_party(request)
    account = _translate(lambda: second_factor.finish_login_step(body.get("etape", ""), method, answer, rp_id, origins))
    if not account:
        server_password.record_failure()
        guard.log_failed_login(request, f"{METHOD_NAMES.get(method, method)} refusée")
        raise HTTPException(403, "Vérification refusée. Réessaie.")
    if method == "secours":
        left = second_factor.summary(account["identifiant"])["codes_restants"]
        _log(request, f"Code de secours utilisé par {account['identifiant']} (il en reste {left})")
    return admin_session.open_admin_session(request, account)
