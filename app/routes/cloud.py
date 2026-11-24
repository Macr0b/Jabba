"""API des comptes Nextcloud : l'écran Comptes, les réglages, les inscriptions et chaque compte.

Sommaire :
  outils communs : format_gigabytes, run_cloud_action, set_rank
  GET  /api/nuage                    cloud_overview
  POST /api/nuage/mettre-en-route    set_up_cloud
  POST /api/nuage/reglages           cloud_settings
  POST /api/nuage/charte             cloud_charter
  GET  /api/nuage/attente            pending_signups
  POST /api/nuage/comptes            add_cloud_account
  POST /api/nuage/comptes/{uid}      cloud_account_action (un geste de ACCOUNT_ACTIONS)
  interne : _approve, _refuse, _suspend, _change_rank, _delete, _set_quota, _set_group, _new_password
Rangs, groupes et dossiers communs : routes/cloud_teams.py.
"""

from fastapi import APIRouter, HTTPException, Request

from host import commands
from security import accounts, guard
from services import activity_log, nextcloud_accounts
from services import cloud_overview as cloud_overview_service

router = APIRouter()


def format_gigabytes(gigabytes):
    """12.5 → « 12,5 Go » ; None → « illimité »."""
    return (
        "illimité"
        if gigabytes is None
        else str(round(float(gigabytes), 1)).removesuffix(".0").replace(".", ",") + " Go"
    )


def run_cloud_action(function, *a):
    try:
        return function(commands.occ, *a)
    except nextcloud_accounts.CloudError as error:
        raise HTTPException(400, str(error))


@router.get("/api/nuage")
def cloud_overview(request: Request):
    return cloud_overview_service.overview(guard.require_admin(request))


@router.post("/api/nuage/mettre-en-route")
def set_up_cloud(request: Request):
    guard.require_super_admin(request, strong=True)
    run_cloud_action(nextcloud_accounts.set_up)
    activity_log.record(
        "nuage",
        "Comptes Nextcloud mis en route : inscription avec validation, 5 Go par défaut, dossiers communs",
        request.state.nom,
    )
    return {"ok": True}


@router.post("/api/nuage/reglages")
async def cloud_settings(request: Request):
    guard.require_super_admin(request)
    body = await request.json()
    cloud_state = nextcloud_accounts.read()
    if "plafond_go" in body:
        gigabytes = body["plafond_go"]
        if not isinstance(gigabytes, (int, float)) or not 1 <= gigabytes <= 100000:
            raise HTTPException(400, "Plafond invalide.")
        cloud_state["plafond_go"] = gigabytes
        nextcloud_accounts.write(cloud_state)
        activity_log.record("nuage", f"Plafond d'espace par compte : {format_gigabytes(gigabytes)}", request.state.nom)
    if "quota_defaut_go" in body:
        gigabytes = body["quota_defaut_go"]
        if not isinstance(gigabytes, (int, float)) or not 0 < gigabytes <= cloud_state["plafond_go"]:
            raise HTTPException(400, f"Espace par défaut : entre 0 et {cloud_state['plafond_go']} Go (le plafond).")
        run_cloud_action(nextcloud_accounts.set_default_quota, gigabytes)
        activity_log.record(
            "nuage", f"Espace par défaut des nouveaux comptes : {format_gigabytes(gigabytes)}", request.state.nom
        )
    return {"ok": True}


@router.post("/api/nuage/charte")
async def cloud_charter(request: Request):
    """La charte affichée sur le formulaire d'inscription (super admin)."""
    guard.require_super_admin(request)
    text = (await request.json()).get("texte", "")
    run_cloud_action(nextcloud_accounts.set_charter, text)
    activity_log.record(
        "nuage", "Charte d'inscription " + ("modifiée" if str(text).strip() else "retirée"), request.state.nom
    )
    return {"ok": True}


@router.get("/api/nuage/attente")
def pending_signups(request: Request):
    """Nombre d'inscriptions qui attendent (léger : pour la pastille de l'Établi)."""
    guard.require_admin(request)
    return {"attente": len(nextcloud_accounts.read()["en_attente_vus"])}


@router.post("/api/nuage/comptes")
async def add_cloud_account(request: Request):
    """Un compte créé directement par un super admin (pour quelqu'un qui ne passe pas par l'inscription)."""
    guard.require_super_admin(request)
    body = await request.json()
    user_id = str(body.get("id", "")).strip().lower()
    password = accounts.temporary_password()
    run_cloud_action(
        nextcloud_accounts.add,
        user_id,
        str(body.get("nom", "")).strip()[:60],
        str(body.get("email", "")).strip()[:120],
        password,
    )
    text = set_rank(user_id, body.get("rang")) if body.get("rang") else ""
    activity_log.record("nuage", f"Compte Nextcloud créé : {user_id}{text}", request.state.nom)
    nextcloud_accounts.refresh_usage_in_background([user_id])
    return {"ok": True, "mdp": password}


# ---------------------------------------------------------------- un geste sur un compte
# Chaque geste : (account, body, session, who) -> réponse ; le Journal note ce qui a été fait.
def _approve(account, body, session, who):
    run_cloud_action(nextcloud_accounts.approve, account["id"])
    text = set_rank(account["id"], body["rang"]) if body.get("rang") else ""
    activity_log.record("nuage", f"Inscription validée : {account['nom']} ({account['id']}){text}", who)


def _refuse(account, body, session, who):
    run_cloud_action(nextcloud_accounts.delete, account["id"])
    activity_log.record("nuage", f"Inscription refusée : {account['nom']} ({account['id']}), compte supprimé", who)


def _suspend(account, body, session, who):
    active = bool(body.get("actif"))
    run_cloud_action(nextcloud_accounts.suspend, account["id"], active)
    word = "réactivé" if active else "suspendu"
    activity_log.record("nuage", f"Compte {word} : {account['nom']} ({account['id']})", who)


def _change_rank(account, body, session, who):
    cloud_state = nextcloud_accounts.read()
    before = nextcloud_accounts.rank_name(cloud_state, cloud_state.get("rang_de", {}).get(account["id"]))
    set_rank(account["id"], body.get("rang"))
    after = nextcloud_accounts.rank_name(nextcloud_accounts.read(), body.get("rang"))
    text = f"Rang de {account['nom']} ({account['id']}) : {before or 'aucun'} → {after or 'aucun'}"
    activity_log.record("nuage", text, who)


def _delete(account, body, session, who):
    run_cloud_action(nextcloud_accounts.delete, account["id"])
    cloud_state = nextcloud_accounts.read()
    if cloud_state.get("rang_de", {}).pop(account["id"], None):
        nextcloud_accounts.write(cloud_state)
    activity_log.record("nuage", f"Compte supprimé avec ses fichiers : {account['nom']} ({account['id']})", who)


def _set_quota(account, body, session, who):
    gigabytes = body.get("go")
    limit = nextcloud_accounts.read()["plafond_go"]
    if gigabytes is None:
        if session["rang"] != "super":
            raise HTTPException(403, "Seul un super admin peut donner un espace illimité.")
    elif not isinstance(gigabytes, (int, float)) or gigabytes <= 0:
        raise HTTPException(400, "Espace invalide.")
    elif gigabytes > limit and session["rang"] != "super":
        raise HTTPException(403, f"Au-delà du plafond ({limit} Go) : seul un super admin peut le faire.")
    before = "illimité" if account["illimite"] else format_gigabytes(account["quota"] / nextcloud_accounts.GIGABYTE)
    run_cloud_action(nextcloud_accounts.set_quota, account["id"], gigabytes)
    text = f"Espace de {account['nom']} ({account['id']}) : {before} → {format_gigabytes(gigabytes)}"
    activity_log.record("nuage", text, who)


def _set_group(account, body, session, who):
    group, member = str(body.get("groupe", "")), bool(body.get("dedans"))
    if group.startswith(nextcloud_accounts.RANK_PREFIX):
        raise HTTPException(400, "Pour les rangs, change le rang de la personne.")
    run_cloud_action(nextcloud_accounts.set_group_member, group, account["id"], member)
    word = "ajouté au" if member else "retiré du"
    activity_log.record("nuage", f"{account['nom']} ({account['id']}) {word} groupe {group}", who)


def _new_password(account, body, session, who):
    password = accounts.temporary_password()
    run_cloud_action(nextcloud_accounts.set_password, account["id"], password)
    activity_log.record("nuage", f"Nouveau mot de passe Nextcloud pour {account['nom']} ({account['id']})", who)
    return {"ok": True, "mdp": password}


ACCOUNT_ACTIONS = {
    "valider": _approve,
    "refuser": _refuse,
    "suspendre": _suspend,
    "rang": _change_rank,
    "supprimer": _delete,
    "quota": _set_quota,
    "groupe": _set_group,
    "mdp": _new_password,
}


@router.post("/api/nuage/comptes/{uid}")
async def cloud_account_action(uid: str, request: Request):
    body = await request.json()
    action = body.get("action")
    rank = "admin" if action in ("quota", "groupe") else "super"  # rang, valider… : super admin
    session = guard.require_admin(request, rank, strong=action in ("refuser", "supprimer", "mdp"))
    if action not in ACCOUNT_ACTIONS:
        raise HTTPException(400, "Action inconnue.")
    account = {item["id"]: item for item in run_cloud_action(nextcloud_accounts.accounts)}.get(uid)
    if not account:
        raise HTTPException(404, "Compte inconnu.")
    if account["admin"] and action in ("suspendre", "supprimer", "refuser"):
        raise HTTPException(403, "C'est un compte administrateur de Nextcloud : à gérer dans Nextcloud.")
    return ACCOUNT_ACTIONS[action](account, body, session, request.state.nom) or {"ok": True}


def set_rank(user_id, rank_id):
    """Donne le rang `rid` (None : aucun) et met les groupes à jour. Renvoie « , rang X » pour le Journal."""
    with nextcloud_accounts._lock:
        cloud_state = nextcloud_accounts.read()
        if rank_id and rank_id not in [rank["id"] for rank in nextcloud_accounts.ranks_of(cloud_state)]:
            raise HTTPException(400, "Rang inconnu.")
        if rank_id:
            cloud_state.setdefault("rang_de", {})[user_id] = rank_id
        else:
            cloud_state.setdefault("rang_de", {}).pop(user_id, None)
        nextcloud_accounts.write(cloud_state)
    run_cloud_action(nextcloud_accounts.sync_ranks, cloud_state, {user_id})
    return f", rang {nextcloud_accounts.rank_name(cloud_state, rank_id)}" if rank_id else ""
