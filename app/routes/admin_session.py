"""API de connexion admin : se connecter, confirmer, se déconnecter, appareils connectés.

Sommaire :
  POST /api/admin/connexion          log_in
  POST /api/admin/confirmer          confirm_password (actions sensibles)
  POST /api/admin/deconnexion        log_out
  GET  /api/sessions                 list_sessions
  POST /api/sessions/{sid}/fermer    close_session
  GET  /api/admin/etat               admin_state
  POST /api/admin/mot-de-passe       change_server_password
  POST /api/admin/code               create_recovery_code
  POST /api/admin/secours            use_recovery_code
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from security import guard, server_password, sessions
from services import activity_log, notifications

router = APIRouter()


@router.post("/api/admin/connexion")
async def log_in(request: Request):
    body = await request.json()
    account = guard.check_login(request, body.get("identifiant"), body.get("mot_de_passe", ""))
    temporary = bool(account.get("provisoire"))
    activity_log.record(
        "admin",
        f"Connexion admin : {account['identifiant']}" + (" (mot de passe provisoire)" if temporary else ""),
        request.state.nom,
    )
    sessions.close(request.state.jeton)
    response = JSONResponse(guard.session_info(account))
    guard.open_session(response, request, account)
    return response


@router.post("/api/admin/confirmer")
async def confirm_password(request: Request):
    """Redemander le mot de passe avant une action sensible."""
    session = guard.require_admin(request)
    guard.check_login(request, session["identifiant"], (await request.json()).get("mot_de_passe", ""))
    sessions.confirm(request.state.jeton)
    return {"ok": True}


@router.post("/api/admin/deconnexion")
def log_out(request: Request):
    sessions.close(request.state.jeton)
    response = JSONResponse({"ok": True})
    response.delete_cookie(sessions.COOKIE, secure=True, httponly=True, samesite="strict")
    return response


@router.get("/api/sessions")
def list_sessions(request: Request):
    session = guard.require_admin(request)
    current = session["id"]
    all_entries = sessions.list_sessions()
    if session["rang"] != "super":
        all_entries = [
            session_entry for session_entry in all_entries if session_entry["identifiant"] == session["identifiant"]
        ]
    return [{**session_entry, "actuelle": session_entry["id"] == current} for session_entry in all_entries]


@router.post("/api/sessions/{sid}/fermer")
def close_session(sid: str, request: Request):
    session = guard.require_admin(request)
    target = next((session_entry for session_entry in sessions.list_sessions() if session_entry["id"] == sid), None)
    if not target:
        raise HTTPException(404, "Appareil introuvable.")
    if target["identifiant"] != session["identifiant"] and session["rang"] != "super":
        raise HTTPException(403, "Tu ne peux déconnecter que tes propres appareils.")
    sessions.close_by_id(sid)
    activity_log.record(
        "admin",
        f"Appareil déconnecté : {target['identifiant']}{' (' + target['appareil'] + ')' if target.get('appareil') else ''}, par {session['identifiant']}",
        request.state.nom,
    )
    return {"ok": True}


@router.get("/api/admin/etat")
def admin_state(request: Request):
    session = request.state.session
    return {
        "code_existe": server_password.recovery_code_exists(),
        "utilisateur": guard.server_user(),
        "serveur": guard.hostname(),
        "connecte": {"identifiant": session["identifiant"], "rang": session["rang"]} if session else None,
    }


@router.post("/api/admin/mot-de-passe")
async def change_server_password(request: Request):
    """Mot de passe du compte du serveur (super admin principal) : c'est aussi celui de sudo."""
    body = await request.json()
    if server_password.is_locked():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    if not guard.check_server_password(body.get("ancien", "")):
        guard.log_failed_login(request, guard.hostname())
        raise HTTPException(403, "L'ancien mot de passe est incorrect.")
    guard._change_server_password(body.get("nouveau", ""))
    sessions.close_account(guard.hostname())  # ses appareils devront se reconnecter avec le nouveau
    notifications.send(
        "securite",
        "Mot de passe du serveur changé",
        f"Par {request.state.nom}.",
        "/#/systeme",
        family="admin",
        recipients=guard.super_admin_logins(),
    )
    return {"ok": True}


@router.post("/api/admin/code")
def create_recovery_code(request: Request):
    guard.require_server_account(request)
    activity_log.record("admin", "Nouveau code de secours créé", request.state.nom)
    return {"code": server_password.generate_recovery_code()}


@router.post("/api/admin/secours")
async def use_recovery_code(request: Request):
    body = await request.json()
    if server_password.is_locked():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    if not server_password.check_recovery_code(body.get("code", "")):
        guard.log_failed_login(request, "code de secours")
        raise HTTPException(403, "Code de secours incorrect.")
    guard._change_server_password(body.get("nouveau", ""))
    sessions.close_account(guard.hostname())
    notifications.send(
        "securite",
        "Mot de passe du serveur changé",
        f"Avec le code de secours, par {request.state.nom}.",
        "/#/systeme",
        family="admin",
        recipients=guard.super_admin_logins(),
    )
    return {"ok": True, "code": server_password.generate_recovery_code()}
