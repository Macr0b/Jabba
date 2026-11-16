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
async def api_admin_connexion(request: Request):
    corps = await request.json()
    compte = guard.verifier_identifiants(request, corps.get("identifiant"), corps.get("mot_de_passe", ""))
    provisoire = bool(compte.get("provisoire"))
    activity_log.noter("admin", f"Connexion admin : {compte['identifiant']}" + (" (mot de passe provisoire)" if provisoire else ""), request.state.nom)
    sessions.fermer(request.state.jeton)
    r = JSONResponse(guard.infos_session(compte))
    guard.poser_session(r, request, compte)
    return r


@router.post("/api/admin/confirmer")
async def api_admin_confirmer(request: Request):
    """Redemander le mot de passe avant une action sensible."""
    j = guard.exiger_admin(request)
    guard.verifier_identifiants(request, j["identifiant"], (await request.json()).get("mot_de_passe", ""))
    sessions.confirmer(request.state.jeton)
    return {"ok": True}


@router.post("/api/admin/deconnexion")
def api_admin_deconnexion(request: Request):
    sessions.fermer(request.state.jeton)
    r = JSONResponse({"ok": True})
    r.delete_cookie(sessions.COOKIE, secure=True, httponly=True, samesite="strict")
    return r


@router.get("/api/sessions")
def api_sessions(request: Request):
    j = guard.exiger_admin(request)
    actuelle = j["id"]
    toutes = sessions.lister()
    if j["rang"] != "super":
        toutes = [x for x in toutes if x["identifiant"] == j["identifiant"]]
    return [{**x, "actuelle": x["id"] == actuelle} for x in toutes]


@router.post("/api/sessions/{sid}/fermer")
def api_sessions_fermer(sid: str, request: Request):
    j = guard.exiger_admin(request)
    cible = next((x for x in sessions.lister() if x["id"] == sid), None)
    if not cible:
        raise HTTPException(404, "Appareil introuvable.")
    if cible["identifiant"] != j["identifiant"] and j["rang"] != "super":
        raise HTTPException(403, "Tu ne peux déconnecter que tes propres appareils.")
    sessions.fermer_id(sid)
    activity_log.noter("admin", f"Appareil déconnecté : {cible['identifiant']}{' (' + cible['appareil'] + ')' if cible.get('appareil') else ''}, par {j['identifiant']}", request.state.nom)
    return {"ok": True}


@router.get("/api/admin/etat")
def api_admin_etat(request: Request):
    j = request.state.session
    return {"code_existe": server_password.code_existe(), "utilisateur": guard.admin_user(), "serveur": guard.nom_hote(),
            "connecte": {"identifiant": j["identifiant"], "rang": j["rang"]} if j else None}


@router.post("/api/admin/mot-de-passe")
async def api_admin_mdp(request: Request):
    """Mot de passe du compte du serveur (super admin principal) : c'est aussi celui de sudo."""
    corps = await request.json()
    if server_password.bloque():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    if not guard.verifier_admin(corps.get("ancien", "")):
        guard.echec_admin(request, guard.nom_hote())
        raise HTTPException(403, "L'ancien mot de passe est incorrect.")
    guard._changer(corps.get("nouveau", ""))
    sessions.fermer_compte(guard.nom_hote())   # ses appareils devront se reconnecter avec le nouveau
    notifications.envoyer("securite", "Mot de passe du serveur changé", f"Par {request.state.nom}.", "/#/systeme", famille="admin", pour=guard.pour_super())
    return {"ok": True}


@router.post("/api/admin/code")
def api_admin_code(request: Request):
    guard.exiger_compte_serveur(request)
    activity_log.noter("admin", "Nouveau code de secours créé", request.state.nom)
    return {"code": server_password.generer_code()}


@router.post("/api/admin/secours")
async def api_admin_secours(request: Request):
    corps = await request.json()
    if server_password.bloque():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    if not server_password.verifier_code(corps.get("code", "")):
        guard.echec_admin(request, "code de secours")
        raise HTTPException(403, "Code de secours incorrect.")
    guard._changer(corps.get("nouveau", ""))
    sessions.fermer_compte(guard.nom_hote())
    notifications.envoyer("securite", "Mot de passe du serveur changé", f"Avec le code de secours, par {request.state.nom}.", "/#/systeme",
                   famille="admin", pour=guard.pour_super())
    return {"ok": True, "code": server_password.generer_code()}
