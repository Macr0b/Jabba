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
    session = guard.require_admin_space(request)
    accounts_list = accounts.list_accounts()
    server = {
        "id": "serveur",
        "identifiant": guard.hostname(),
        "nom": "Compte du serveur",
        "rang": "super",
        "tailscale": None,
        "systeme": True,
    }
    return {
        "moi": {"identifiant": session["identifiant"], "rang": session["rang"]},
        "comptes": [server] + accounts_list["comptes"],
        "reglages": accounts_list["reglages"],
        "demandes": accounts_list["demandes"] if session["rang"] == "super" else [],
        "demandes_n": len(accounts_list["demandes"]),
        "proprietaires": sorted(config.TAILSCALE_USERS),
        **accounts.recent_logins(),
    }


@router.post("/api/comptes")
async def create_account(request: Request):
    session = guard.require_admin_space(request)
    body = await request.json()
    account, password = guard.translate_account_refusal(
        lambda: accounts.create(
            body.get("identifiant"),
            body.get("nom"),
            body.get("rang", "admin"),
            body.get("tailscale"),
            session["identifiant"],
            session["rang"],
            guard.server_identifiers(),
        )
    )
    activity_log.record(
        "admin",
        f"Compte ajouté : {account['identifiant']} ({'super admin' if account['rang'] == 'super' else 'admin'}), par {session['identifiant']}",
        request.state.nom,
    )
    notifications.send(
        "comptes",
        "Compte ajouté",
        f"{account['nom']} ({account['identifiant']}), par {session['identifiant']}.",
        "/#/utilisateurs",
        family="admin",
        recipients=guard.super_admin_logins(),
        log=False,
    )
    if account["tailscale"]:
        notifications.send(
            "comptes",
            "Ton compte admin est prêt",
            "Ouvre l'appli Jabba : ton mot de passe provisoire s'affiche.",
            "/",
            recipients={account["tailscale"]},
            log=False,
            force=True,
        )
        return {"compte": account, "remis": True}
    return {"compte": account, "mot_de_passe": password}


@router.post("/api/comptes/{cid}")
async def update_account(cid: str, request: Request):
    session = guard.require_admin_space(request)
    body = await request.json()
    action = body.get("action")
    if action in ("supprimer", "rang", "terminal"):
        guard.require_admin(request, strong=True)
    if action == "supprimer":
        account = guard.translate_account_refusal(lambda: accounts.delete(cid, session["rang"]))
        sessions.close_account(account["identifiant"])
        text = f"Compte retiré : {account['identifiant']}"
    elif action == "rang":
        account = guard.translate_account_refusal(lambda: accounts.update(cid, session["rang"], role=body.get("rang")))
        sessions.close_account(account["identifiant"])
        text = f"{account['identifiant']} est maintenant {'super admin' if account['rang'] == 'super' else 'admin'}"
    elif action == "tailscale":
        account = guard.translate_account_refusal(
            lambda: accounts.update(cid, session["rang"], tailscale=body.get("tailscale") or None)
        )
        text = f"{account['identifiant']} : compte Tailscale {account['tailscale'] or 'retiré'}"
    elif action == "terminal":
        account = guard.translate_account_refusal(
            lambda: accounts.update(cid, session["rang"], terminal_allowed=bool(body.get("terminal")))
        )
        text = f"Terminal {'autorisé' if account['terminal'] else 'retiré'} pour {account['identifiant']}"
    elif action == "nom":
        account = guard.translate_account_refusal(
            lambda: accounts.update(cid, session["rang"], display_name=body.get("nom") or "")
        )
        text = f"{account['identifiant']} renommé « {account['nom']} »"
    else:
        raise HTTPException(400, "Action inconnue.")
    activity_log.record("admin", f"{text}, par {session['identifiant']}", request.state.nom)
    if action in ("supprimer", "rang", "terminal"):
        notifications.send(
            "comptes",
            "Comptes modifiés",
            f"{text}, par {session['identifiant']}.",
            "/#/utilisateurs",
            family="admin",
            recipients=guard.super_admin_logins(),
            log=False,
        )
    return {"ok": True, "compte": account}


@router.post("/api/comptes-reglages")
async def update_account_settings(request: Request):
    session = guard.require_super_admin(request, strong=True)
    opened = bool((await request.json()).get("espace_admins"))
    accounts.set_settings(opened)
    activity_log.record(
        "admin",
        f"Espace Utilisateurs {'ouvert à tous les admins' if opened else 'réservé au super admin'}, par {session['identifiant']}",
        request.state.nom,
    )
    return {"ok": True}


@router.post("/api/comptes-moi/mot-de-passe")
async def change_my_password(request: Request):
    """Un admin (compte de l'appli) change son propre mot de passe. Accepté avec une session provisoire."""
    session = request.state.session
    if not session or session["identifiant"] == guard.hostname():
        raise HTTPException(401, {"admin": True, "message": "Connexion admin demandée."})
    body = await request.json()
    if not session.get("provisoire") and not accounts.check_credentials(session["identifiant"], body.get("ancien", "")):
        raise HTTPException(403, "L'ancien mot de passe est incorrect.")
    guard.translate_account_refusal(
        lambda: accounts.change_own_password(session["identifiant"], body.get("nouveau", ""))
    )
    sessions.close_account(session["identifiant"])  # déconnecte ses autres appareils
    account = accounts.find(session["identifiant"])
    activity_log.record("admin", f"{session['identifiant']} a choisi un nouveau mot de passe", request.state.nom)
    response = JSONResponse(guard.session_info({**account, "provisoire": False}))
    guard.open_session(response, request, {**account, "provisoire": False})
    return response


# ---------------------------------------------------------------- comptes : mot de passe oublié
@router.post("/api/comptes-oubli")
async def forgotten_password(request: Request):
    body = await request.json()
    if server_password.is_locked():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    identifier = (body.get("identifiant") or "").strip().lower()
    if identifier in guard.server_identifiers():
        return {"ok": True, "serveur": True}
    account = accounts.request_reset(identifier, request.state.user, request.state.nom)
    if account:
        activity_log.record("admin", f"Mot de passe oublié : demande pour {account['identifiant']}", request.state.nom)
        notifications.send(
            "comptes",
            "Demande de mot de passe",
            f"{account['nom']} ({account['identifiant']}) a oublié son mot de passe. Ouvre l'appli pour accepter ou refuser.",
            "/#/utilisateurs",
            family="admin",
            recipients=guard.super_admin_logins(),
            log=False,
        )
    else:
        server_password.record_failure()
    # Même réponse dans tous les cas : on ne dit pas si l'identifiant existe.
    return {"ok": True}


@router.post("/api/comptes-demandes/{did}")
async def answer_reset_request(did: str, request: Request):
    session = guard.require_super_admin(request, strong=True)
    accept = bool((await request.json()).get("accepter"))
    reset_request, _ = guard.translate_account_refusal(
        lambda: accounts.answer_reset(did, accept, session["identifiant"])
    )
    sessions.close_account(reset_request["identifiant"])
    activity_log.record(
        "admin",
        f"Demande de {reset_request['identifiant']} {'acceptée' if accept else 'refusée'}, par {session['identifiant']}",
        request.state.nom,
    )
    if accept:
        notifications.send(
            "comptes",
            "Demande acceptée",
            "Ouvre l'appli Jabba : ton mot de passe provisoire s'affiche.",
            "/",
            recipients={reset_request.get("remis_a") or reset_request["login"]},
            log=False,
            force=True,
        )
    else:
        notifications.send(
            "comptes",
            "Demande refusée",
            "Vois avec le super admin.",
            "/",
            recipients={reset_request["login"]},
            log=False,
            force=True,
        )
    return {"ok": True}


@router.post("/api/comptes-remise")
def collect_reset(request: Request):
    """Le mot de passe provisoire, une seule fois, sur l'appareil de la personne concernée."""
    response = accounts.collect_reset(request.state.user)
    if not response:
        raise HTTPException(404, "Rien à récupérer.")
    activity_log.record("admin", f"Mot de passe provisoire affiché pour {response['identifiant']}", request.state.nom)
    return response
