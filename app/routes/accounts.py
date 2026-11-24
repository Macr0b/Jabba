"""API des comptes admin de Jabba : liste, création, rangs, mot de passe oublié.

Sommaire :
  GET  /api/comptes                    list_accounts
  POST /api/comptes                    create_account
  POST /api/comptes/{cid}              update_account (rang, Tailscale, nom, suppression)
  POST /api/comptes-reglages           update_account_settings
  POST /api/comptes-moi/mot-de-passe   change_my_password
  POST /api/comptes-oubli              forgotten_password (demande de remise)
  POST /api/comptes-demandes/{did}     answer_reset_request (le super admin accepte ou refuse)
  POST /api/comptes-remise             collect_reset (le mot de passe provisoire, une seule fois)
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import config
from security import accounts, guard, second_factor, server_password, sessions
from services import activity_log, notifications

router = APIRouter()


@router.get("/api/comptes")
def list_accounts(request: Request):
    session = guard.require_admin_space(request)
    accounts_list = accounts.list_accounts()
    server = {
        "id": "serveur",
        "username": guard.hostname(),
        "name": "Compte du serveur",
        "rank": "super",
        "tailscale": None,
        "system": True,
    }
    return {
        "me": {"username": session["username"], "rank": session["rank"]},
        "accounts": [server] + accounts_list["accounts"],
        "settings": accounts_list["settings"],
        "requests": accounts_list["requests"] if session["rank"] == "super" else [],
        "request_count": len(accounts_list["requests"]),
        "owners": sorted(config.TAILSCALE_USERS),
        **accounts.recent_logins(),
    }


@router.post("/api/comptes")
async def create_account(request: Request):
    session = guard.require_admin_space(request)
    body = await request.json()
    account, password = guard.translate_account_refusal(
        lambda: accounts.create(
            body.get("username"),
            body.get("name"),
            body.get("rank", "admin"),
            body.get("tailscale"),
            session["username"],
            session["rank"],
            guard.server_identifiers(),
        )
    )
    activity_log.record(
        "admin",
        f"Compte ajouté : {account['username']} ({'super admin' if account['rank'] == 'super' else 'admin'}), par {session['username']}",
        request.state.nom,
    )
    notifications.send(
        "comptes",
        "Compte ajouté",
        f"{account['name']} ({account['username']}), par {session['username']}.",
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
        return {"account": account, "reset": True}
    return {"account": account, "password": password}


@router.post("/api/comptes/{cid}")
async def update_account(cid: str, request: Request):
    session = guard.require_admin_space(request)
    body = await request.json()
    action = body.get("action")
    if action in ("supprimer", "rang"):
        guard.require_admin(request, strong=True)
    if action == "supprimer":
        account = guard.translate_account_refusal(lambda: accounts.delete(cid, session["rank"]))
        sessions.close_account(account["username"])
        second_factor.clear(account["username"])
        text = f"Compte retiré : {account['username']}"
    elif action == "rang":
        account = guard.translate_account_refusal(lambda: accounts.update(cid, session["rank"], role=body.get("rank")))
        sessions.close_account(account["username"])
        text = f"{account['username']} est maintenant {'super admin' if account['rank'] == 'super' else 'admin'}"
    elif action == "tailscale":
        account = guard.translate_account_refusal(
            lambda: accounts.update(cid, session["rank"], tailscale=body.get("tailscale") or None)
        )
        text = f"{account['username']} : compte Tailscale {account['tailscale'] or 'retiré'}"
    elif action == "nom":
        account = guard.translate_account_refusal(
            lambda: accounts.update(cid, session["rank"], display_name=body.get("name") or "")
        )
        text = f"{account['username']} renommé « {account['name']} »"
    else:
        raise HTTPException(400, "Action inconnue.")
    activity_log.record("admin", f"{text}, par {session['username']}", request.state.nom)
    if action in ("supprimer", "rang"):
        notifications.send(
            "comptes",
            "Comptes modifiés",
            f"{text}, par {session['username']}.",
            "/#/utilisateurs",
            family="admin",
            recipients=guard.super_admin_logins(),
            log=False,
        )
    return {"ok": True, "account": account}


@router.post("/api/comptes-reglages")
async def update_account_settings(request: Request):
    session = guard.require_super_admin(request, strong=True)
    opened = bool((await request.json()).get("admin_space"))
    accounts.set_settings(opened)
    activity_log.record(
        "admin",
        f"Espace Utilisateurs {'ouvert à tous les admins' if opened else 'réservé au super admin'}, par {session['username']}",
        request.state.nom,
    )
    return {"ok": True}


@router.post("/api/comptes-moi/mot-de-passe")
async def change_my_password(request: Request):
    """Un admin (compte de l'appli) change son propre mot de passe. Accepté avec une session provisoire."""
    session = request.state.session
    if not session or session["username"] == guard.hostname():
        raise HTTPException(401, {"admin": True, "message": "Connexion admin demandée."})
    body = await request.json()
    if not session.get("temporary") and not accounts.check_credentials(session["username"], body.get("old", "")):
        raise HTTPException(403, "L'ancien mot de passe est incorrect.")
    guard.translate_account_refusal(lambda: accounts.change_own_password(session["username"], body.get("new", "")))
    sessions.close_account(session["username"])  # déconnecte ses autres appareils
    account = accounts.find(session["username"])
    activity_log.record("admin", f"{session['username']} a choisi un nouveau mot de passe", request.state.nom)
    response = JSONResponse(guard.session_info({**account, "temporary": False}))
    guard.open_session(response, request, {**account, "temporary": False})
    return response


# ---------------------------------------------------------------- comptes : mot de passe oublié
@router.post("/api/comptes-oubli")
async def forgotten_password(request: Request):
    body = await request.json()
    if server_password.is_locked():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    identifier = (body.get("username") or "").strip().lower()
    if identifier in guard.server_identifiers():
        return {"ok": True, "server": True}
    account = accounts.request_reset(identifier, request.state.user, request.state.nom)
    if account:
        activity_log.record("admin", f"Mot de passe oublié : demande pour {account['username']}", request.state.nom)
        notifications.send(
            "comptes",
            "Demande de mot de passe",
            f"{account['name']} ({account['username']}) a oublié son mot de passe. Ouvre l'appli pour accepter ou refuser.",
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
    reset_request, _ = guard.translate_account_refusal(lambda: accounts.answer_reset(did, accept, session["username"]))
    sessions.close_account(reset_request["username"])
    activity_log.record(
        "admin",
        f"Demande de {reset_request['username']} {'acceptée' if accept else 'refusée'}, par {session['username']}",
        request.state.nom,
    )
    if accept:
        notifications.send(
            "comptes",
            "Demande acceptée",
            "Ouvre l'appli Jabba : ton mot de passe provisoire s'affiche.",
            "/",
            recipients={reset_request.get("reset_to") or reset_request["login"]},
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
    activity_log.record("admin", f"Mot de passe provisoire affiché pour {response['username']}", request.state.nom)
    return response
