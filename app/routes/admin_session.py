"""routes.admin_session"""
from security import server_password
from security import sessions
from services import activity_log
from services import notifications
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from security import guard
from fastapi import APIRouter

router = APIRouter()

@router.post("/api/admin/connexion")
async def log_in(request: Request):
    corps = await request.json()
    compte = guard.check_login(request, corps.get("identifiant"), corps.get("mot_de_passe", ""))
    provisoire = bool(compte.get("provisoire"))
    activity_log.record("admin", f"Connexion admin : {compte['identifiant']}" + (" (mot de passe provisoire)" if provisoire else ""), request.state.nom)
    sessions.close(request.state.jeton)
    r = JSONResponse(guard.session_info(compte))
    guard.open_session(r, request, compte)
    return r


@router.post("/api/admin/confirmer")
async def confirm_password(request: Request):
    """Redemander le mot de passe avant une action sensible."""
    j = guard.require_admin(request)
    guard.check_login(request, j["identifiant"], (await request.json()).get("mot_de_passe", ""))
    sessions.confirm(request.state.jeton)
    return {"ok": True}


@router.post("/api/admin/deconnexion")
def log_out(request: Request):
    sessions.close(request.state.jeton)
    r = JSONResponse({"ok": True})
    r.delete_cookie(sessions.COOKIE, secure=True, httponly=True, samesite="strict")
    return r


@router.get("/api/sessions")
def list_sessions(request: Request):
    j = guard.require_admin(request)
    actuelle = j["id"]
    toutes = sessions.list_sessions()
    if j["rang"] != "super":
        toutes = [x for x in toutes if x["identifiant"] == j["identifiant"]]
    return [{**x, "actuelle": x["id"] == actuelle} for x in toutes]


@router.post("/api/sessions/{sid}/fermer")
def close_session(sid: str, request: Request):
    j = guard.require_admin(request)
    cible = next((x for x in sessions.list_sessions() if x["id"] == sid), None)
    if not cible:
        raise HTTPException(404, "Appareil introuvable.")
    if cible["identifiant"] != j["identifiant"] and j["rang"] != "super":
        raise HTTPException(403, "Tu ne peux déconnecter que tes propres appareils.")
    sessions.close_by_id(sid)
    activity_log.record("admin", f"Appareil déconnecté : {cible['identifiant']}{' (' + cible['appareil'] + ')' if cible.get('appareil') else ''}, par {j['identifiant']}", request.state.nom)
    return {"ok": True}


@router.get("/api/admin/etat")
def admin_state(request: Request):
    j = request.state.session
    return {"code_existe": server_password.recovery_code_exists(), "utilisateur": guard.server_user(), "serveur": guard.hostname(),
            "connecte": {"identifiant": j["identifiant"], "rang": j["rang"]} if j else None}


@router.post("/api/admin/mot-de-passe")
async def change_server_password(request: Request):
    """Mot de passe du compte du serveur (super admin principal) : c'est aussi celui de sudo."""
    corps = await request.json()
    if server_password.is_locked():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    if not guard.check_server_password(corps.get("ancien", "")):
        guard.log_failed_login(request, guard.hostname())
        raise HTTPException(403, "L'ancien mot de passe est incorrect.")
    guard._change_server_password(corps.get("nouveau", ""))
    sessions.close_account(guard.hostname())   # ses appareils devront se reconnecter avec le nouveau
    notifications.send("securite", "Mot de passe du serveur changé", f"Par {request.state.nom}.", "/#/systeme", famille="admin", pour=guard.super_admin_logins())
    return {"ok": True}


@router.post("/api/admin/code")
def create_recovery_code(request: Request):
    guard.require_server_account(request)
    activity_log.record("admin", "Nouveau code de secours créé", request.state.nom)
    return {"code": server_password.generate_recovery_code()}


@router.post("/api/admin/secours")
async def use_recovery_code(request: Request):
    corps = await request.json()
    if server_password.is_locked():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    if not server_password.check_recovery_code(corps.get("code", "")):
        guard.log_failed_login(request, "code de secours")
        raise HTTPException(403, "Code de secours incorrect.")
    guard._change_server_password(corps.get("nouveau", ""))
    sessions.close_account(guard.hostname())
    notifications.send("securite", "Mot de passe du serveur changé", f"Avec le code de secours, par {request.state.nom}.", "/#/systeme",
                   famille="admin", pour=guard.super_admin_logins())
    return {"ok": True, "code": server_password.generate_recovery_code()}
