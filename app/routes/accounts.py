"""routes.accounts"""
from security import server_password
from security import accounts
from security import sessions
from services import activity_log
from services import notifications
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
import config
from security import guard
from fastapi import APIRouter

router = APIRouter()

@router.get("/api/comptes")
def list_accounts(request: Request):
    j = guard.require_admin_space(request)
    d = accounts.list_accounts()
    serveur = {"id": "serveur", "identifiant": guard.hostname(), "nom": "Compte du serveur", "rang": "super",
               "tailscale": None, "systeme": True}
    return {"moi": {"identifiant": j["identifiant"], "rang": j["rang"]}, "comptes": [serveur] + d["comptes"],
            "reglages": d["reglages"], "demandes": d["demandes"] if j["rang"] == "super" else [],
            "demandes_n": len(d["demandes"]), "proprietaires": sorted(config.TAILSCALE_USERS), **accounts.recent_logins()}


@router.post("/api/comptes")
async def create_account(request: Request):
    j = guard.require_admin_space(request)
    corps = await request.json()
    compte, mdp = guard.translate_account_refusal(lambda: accounts.create(corps.get("identifiant"), corps.get("nom"), corps.get("rang", "admin"),
                                            corps.get("tailscale"), j["identifiant"], j["rang"], guard.server_identifiers()))
    activity_log.record("admin", f"Compte ajouté : {compte['identifiant']} ({'super admin' if compte['rang'] == 'super' else 'admin'}), par {j['identifiant']}", request.state.nom)
    notifications.send("comptes", "Compte ajouté", f"{compte['nom']} ({compte['identifiant']}), par {j['identifiant']}.", "/#/utilisateurs",
                   famille="admin", pour=guard.super_admin_logins(), noter=False)
    if compte["tailscale"]:
        notifications.send("comptes", "Ton compte admin est prêt", "Ouvre l'appli Jabba : ton mot de passe provisoire s'affiche.", "/",
                       pour={compte["tailscale"]}, noter=False, forcer=True)
        return {"compte": compte, "remis": True}
    return {"compte": compte, "mot_de_passe": mdp}


@router.post("/api/comptes/{cid}")
async def update_account(cid: str, request: Request):
    j = guard.require_admin_space(request)
    corps = await request.json()
    action = corps.get("action")
    if action in ("supprimer", "rang", "terminal"):
        guard.require_admin(request, fort=True)
    if action == "supprimer":
        c = guard.translate_account_refusal(lambda: accounts.delete(cid, j["rang"]))
        sessions.close_account(c["identifiant"])
        texte = f"Compte retiré : {c['identifiant']}"
    elif action == "rang":
        c = guard.translate_account_refusal(lambda: accounts.update(cid, j["rang"], rang=corps.get("rang")))
        sessions.close_account(c["identifiant"])
        texte = f"{c['identifiant']} est maintenant {'super admin' if c['rang'] == 'super' else 'admin'}"
    elif action == "tailscale":
        c = guard.translate_account_refusal(lambda: accounts.update(cid, j["rang"], tailscale=corps.get("tailscale") or None))
        texte = f"{c['identifiant']} : compte Tailscale {c['tailscale'] or 'retiré'}"
    elif action == "terminal":
        c = guard.translate_account_refusal(lambda: accounts.update(cid, j["rang"], terminal=bool(corps.get("terminal"))))
        texte = f"Terminal {'autorisé' if c['terminal'] else 'retiré'} pour {c['identifiant']}"
    elif action == "nom":
        c = guard.translate_account_refusal(lambda: accounts.update(cid, j["rang"], nom=corps.get("nom") or ""))
        texte = f"{c['identifiant']} renommé « {c['nom']} »"
    else:
        raise HTTPException(400, "Action inconnue.")
    activity_log.record("admin", f"{texte}, par {j['identifiant']}", request.state.nom)
    if action in ("supprimer", "rang", "terminal"):
        notifications.send("comptes", "Comptes modifiés", f"{texte}, par {j['identifiant']}.", "/#/utilisateurs",
                       famille="admin", pour=guard.super_admin_logins(), noter=False)
    return {"ok": True, "compte": c}


@router.post("/api/comptes-reglages")
async def update_account_settings(request: Request):
    j = guard.require_super_admin(request, fort=True)
    ouvert = bool((await request.json()).get("espace_admins"))
    accounts.set_settings(ouvert)
    activity_log.record("admin", f"Espace Utilisateurs {'ouvert à tous les admins' if ouvert else 'réservé au super admin'}, par {j['identifiant']}", request.state.nom)
    return {"ok": True}


@router.post("/api/comptes-moi/mot-de-passe")
async def change_my_password(request: Request):
    """Un admin (compte de l'appli) change son propre mot de passe. Accepté avec une session provisoire."""
    j = request.state.session
    if not j or j["identifiant"] == guard.hostname():
        raise HTTPException(401, {"admin": True, "message": "Connexion admin demandée."})
    corps = await request.json()
    if not j.get("provisoire") and not accounts.check_credentials(j["identifiant"], corps.get("ancien", "")):
        raise HTTPException(403, "L'ancien mot de passe est incorrect.")
    guard.translate_account_refusal(lambda: accounts.change_own_password(j["identifiant"], corps.get("nouveau", "")))
    sessions.close_account(j["identifiant"])   # déconnecte ses autres appareils
    compte = accounts.find(j["identifiant"])
    activity_log.record("admin", f"{j['identifiant']} a choisi un nouveau mot de passe", request.state.nom)
    r = JSONResponse(guard.session_info({**compte, "provisoire": False}))
    guard.open_session(r, request, {**compte, "provisoire": False})
    return r


# ---------------------------------------------------------------- comptes : mot de passe oublié
@router.post("/api/comptes-oubli")
async def forgotten_password(request: Request):
    corps = await request.json()
    if server_password.is_locked():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    ident = (corps.get("identifiant") or "").strip().lower()
    if ident in guard.server_identifiers():
        return {"ok": True, "serveur": True}
    c = accounts.request_reset(ident, request.state.user, request.state.nom)
    if c:
        activity_log.record("admin", f"Mot de passe oublié : demande pour {c['identifiant']}", request.state.nom)
        notifications.send("comptes", "Demande de mot de passe", f"{c['nom']} ({c['identifiant']}) a oublié son mot de passe. Ouvre l'appli pour accepter ou refuser.",
                       "/#/utilisateurs", famille="admin", pour=guard.super_admin_logins(), noter=False)
    else:
        server_password.record_failure()
    # Même réponse dans tous les cas : on ne dit pas si l'identifiant existe.
    return {"ok": True}


@router.post("/api/comptes-demandes/{did}")
async def answer_reset_request(did: str, request: Request):
    j = guard.require_super_admin(request, fort=True)
    accepter = bool((await request.json()).get("accepter"))
    dem, _ = guard.translate_account_refusal(lambda: accounts.answer_reset(did, accepter, j["identifiant"]))
    sessions.close_account(dem["identifiant"])
    activity_log.record("admin", f"Demande de {dem['identifiant']} {'acceptée' if accepter else 'refusée'}, par {j['identifiant']}", request.state.nom)
    if accepter:
        notifications.send("comptes", "Demande acceptée", "Ouvre l'appli Jabba : ton mot de passe provisoire s'affiche.", "/",
                       pour={dem.get("remis_a") or dem["login"]}, noter=False, forcer=True)
    else:
        notifications.send("comptes", "Demande refusée", "Vois avec le super admin.", "/", pour={dem["login"]}, noter=False, forcer=True)
    return {"ok": True}


@router.post("/api/comptes-remise")
def collect_reset(request: Request):
    """Le mot de passe provisoire, une seule fois, sur l'appareil de la personne concernée."""
    r = accounts.collect_reset(request.state.user)
    if not r:
        raise HTTPException(404, "Rien à récupérer.")
    activity_log.record("admin", f"Mot de passe provisoire affiché pour {r['identifiant']}", request.state.nom)
    return r
