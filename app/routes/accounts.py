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
def api_comptes(request: Request):
    j = guard.exiger_espace(request)
    d = accounts.lister()
    serveur = {"id": "serveur", "identifiant": guard.nom_hote(), "nom": "Compte du serveur", "rang": "super",
               "tailscale": None, "systeme": True}
    return {"moi": {"identifiant": j["identifiant"], "rang": j["rang"]}, "comptes": [serveur] + d["comptes"],
            "reglages": d["reglages"], "demandes": d["demandes"] if j["rang"] == "super" else [],
            "demandes_n": len(d["demandes"]), "proprietaires": sorted(config.UTILISATEURS), **accounts.connexions()}


@router.post("/api/comptes")
async def api_comptes_creer(request: Request):
    j = guard.exiger_espace(request)
    corps = await request.json()
    compte, mdp = guard.cmp(lambda: accounts.creer(corps.get("identifiant"), corps.get("nom"), corps.get("rang", "admin"),
                                            corps.get("tailscale"), j["identifiant"], j["rang"], guard.identifiants_serveur()))
    activity_log.noter("admin", f"Compte ajouté : {compte['identifiant']} ({'super admin' if compte['rang'] == 'super' else 'admin'}), par {j['identifiant']}", request.state.nom)
    notifications.envoyer("comptes", "Compte ajouté", f"{compte['nom']} ({compte['identifiant']}), par {j['identifiant']}.", "/#/utilisateurs",
                   famille="admin", pour=guard.pour_super(), noter=False)
    if compte["tailscale"]:
        notifications.envoyer("comptes", "Ton compte admin est prêt", "Ouvre l'appli Jabba : ton mot de passe provisoire s'affiche.", "/",
                       pour={compte["tailscale"]}, noter=False, forcer=True)
        return {"compte": compte, "remis": True}
    return {"compte": compte, "mot_de_passe": mdp}


@router.post("/api/comptes/{cid}")
async def api_comptes_modifier(cid: str, request: Request):
    j = guard.exiger_espace(request)
    corps = await request.json()
    action = corps.get("action")
    if action in ("supprimer", "rang", "terminal"):
        guard.exiger_admin(request, fort=True)
    if action == "supprimer":
        c = guard.cmp(lambda: accounts.supprimer(cid, j["rang"]))
        sessions.fermer_compte(c["identifiant"])
        texte = f"Compte retiré : {c['identifiant']}"
    elif action == "rang":
        c = guard.cmp(lambda: accounts.modifier(cid, j["rang"], rang=corps.get("rang")))
        sessions.fermer_compte(c["identifiant"])
        texte = f"{c['identifiant']} est maintenant {'super admin' if c['rang'] == 'super' else 'admin'}"
    elif action == "tailscale":
        c = guard.cmp(lambda: accounts.modifier(cid, j["rang"], tailscale=corps.get("tailscale") or None))
        texte = f"{c['identifiant']} : compte Tailscale {c['tailscale'] or 'retiré'}"
    elif action == "terminal":
        c = guard.cmp(lambda: accounts.modifier(cid, j["rang"], terminal=bool(corps.get("terminal"))))
        texte = f"Terminal {'autorisé' if c['terminal'] else 'retiré'} pour {c['identifiant']}"
    elif action == "nom":
        c = guard.cmp(lambda: accounts.modifier(cid, j["rang"], nom=corps.get("nom") or ""))
        texte = f"{c['identifiant']} renommé « {c['nom']} »"
    else:
        raise HTTPException(400, "Action inconnue.")
    activity_log.noter("admin", f"{texte}, par {j['identifiant']}", request.state.nom)
    if action in ("supprimer", "rang", "terminal"):
        notifications.envoyer("comptes", "Comptes modifiés", f"{texte}, par {j['identifiant']}.", "/#/utilisateurs",
                       famille="admin", pour=guard.pour_super(), noter=False)
    return {"ok": True, "compte": c}


@router.post("/api/comptes-reglages")
async def api_comptes_reglages(request: Request):
    j = guard.exiger_super(request, fort=True)
    ouvert = bool((await request.json()).get("espace_admins"))
    accounts.regler(ouvert)
    activity_log.noter("admin", f"Espace Utilisateurs {'ouvert à tous les admins' if ouvert else 'réservé au super admin'}, par {j['identifiant']}", request.state.nom)
    return {"ok": True}


@router.post("/api/comptes-moi/mot-de-passe")
async def api_comptes_mien(request: Request):
    """Un admin (compte de l'appli) change son propre mot de passe. Accepté avec une session provisoire."""
    j = request.state.session
    if not j or j["identifiant"] == guard.nom_hote():
        raise HTTPException(401, {"admin": True, "message": "Connexion admin demandée."})
    corps = await request.json()
    if not j.get("provisoire") and not accounts.verifier(j["identifiant"], corps.get("ancien", "")):
        raise HTTPException(403, "L'ancien mot de passe est incorrect.")
    guard.cmp(lambda: accounts.changer_mien(j["identifiant"], corps.get("nouveau", "")))
    sessions.fermer_compte(j["identifiant"])   # déconnecte ses autres appareils
    compte = accounts.trouver(j["identifiant"])
    activity_log.noter("admin", f"{j['identifiant']} a choisi un nouveau mot de passe", request.state.nom)
    r = JSONResponse(guard.infos_session({**compte, "provisoire": False}))
    guard.poser_session(r, request, {**compte, "provisoire": False})
    return r


# ---------------------------------------------------------------- comptes : mot de passe oublié
@router.post("/api/comptes-oubli")
async def api_comptes_oubli(request: Request):
    corps = await request.json()
    if server_password.bloque():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    ident = (corps.get("identifiant") or "").strip().lower()
    if ident in guard.identifiants_serveur():
        return {"ok": True, "serveur": True}
    c = accounts.demander(ident, request.state.user, request.state.nom)
    if c:
        activity_log.noter("admin", f"Mot de passe oublié : demande pour {c['identifiant']}", request.state.nom)
        notifications.envoyer("comptes", "Demande de mot de passe", f"{c['nom']} ({c['identifiant']}) a oublié son mot de passe. Ouvre l'appli pour accepter ou refuser.",
                       "/#/utilisateurs", famille="admin", pour=guard.pour_super(), noter=False)
    else:
        server_password.echec()
    # Même réponse dans tous les cas : on ne dit pas si l'identifiant existe.
    return {"ok": True}


@router.post("/api/comptes-demandes/{did}")
async def api_comptes_repondre(did: str, request: Request):
    j = guard.exiger_super(request, fort=True)
    accepter = bool((await request.json()).get("accepter"))
    dem, _ = guard.cmp(lambda: accounts.repondre(did, accepter, j["identifiant"]))
    sessions.fermer_compte(dem["identifiant"])
    activity_log.noter("admin", f"Demande de {dem['identifiant']} {'acceptée' if accepter else 'refusée'}, par {j['identifiant']}", request.state.nom)
    if accepter:
        notifications.envoyer("comptes", "Demande acceptée", "Ouvre l'appli Jabba : ton mot de passe provisoire s'affiche.", "/",
                       pour={dem.get("remis_a") or dem["login"]}, noter=False, forcer=True)
    else:
        notifications.envoyer("comptes", "Demande refusée", "Vois avec le super admin.", "/", pour={dem["login"]}, noter=False, forcer=True)
    return {"ok": True}


@router.post("/api/comptes-remise")
def api_comptes_remise(request: Request):
    """Le mot de passe provisoire, une seule fois, sur l'appareil de la personne concernée."""
    r = accounts.prendre_remise(request.state.user)
    if not r:
        raise HTTPException(404, "Rien à récupérer.")
    activity_log.noter("admin", f"Mot de passe provisoire affiché pour {r['identifiant']}", request.state.nom)
    return r
