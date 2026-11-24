"""API des rangs, des groupes et des dossiers communs de Nextcloud (écran Comptes).

Sommaire :
  POST /api/nuage/rangs     rank_action (un geste de RANK_ACTIONS ; le compte du serveur seulement)
  POST /api/nuage/groupes   group_action
  POST /api/nuage/dossiers  team_folder_action (un geste de FOLDER_ACTIONS, ou « creer »)
  interne : _rank_switch, _rank_create, _rank_rename, _rank_move, _rank_delete, _folder_create,
            _folder_access, _folder_quota, _folder_rename, _folder_delete, _check_mode
"""

from fastapi import APIRouter, HTTPException, Request

from host import commands
from routes.cloud import format_gigabytes, run_cloud_action
from security import guard
from services import activity_log, nextcloud_accounts

router = APIRouter()


# ---------------------------------------------------------------- rangs
# Chaque geste : (cloud_state, body, rank_id, ids) -> (texte du Journal, tous les comptes à revoir ?).
def _rank_switch(cloud_state, body, rank_id, ids):
    cloud_state["ranks_enabled"] = bool(body.get("active"))
    if cloud_state["ranks_enabled"]:
        return "Rangs activés", True
    return "Rangs désactivés (chacun garde son rang, sans effet jusqu'à la réactivation)", True


def _rank_create(cloud_state, body, rank_id, ids):
    name = str(body.get("name", ""))
    run_cloud_action(nextcloud_accounts.create_rank, cloud_state, name)
    return f"Nouveau rang : {name.strip()}", False


def _rank_rename(cloud_state, body, rank_id, ids):
    name = str(body.get("name", "")).strip()[:40]
    if not name:
        raise HTTPException(400, "Nom vide.")
    before = nextcloud_accounts.rank_name(cloud_state, rank_id)
    next(rank for rank in cloud_state["ranks"] if rank["id"] == rank_id)["name"] = name
    commands.occ(["group:add", "--display-name", name, rank_id])  # sans effet si le groupe existe
    return f"Rang {before} renommé en {name}", False


def _rank_move(cloud_state, body, rank_id, ids):
    index = ids.index(rank_id)
    other = index + (1 if body.get("direction") == "haut" else -1)
    if not 0 <= other < len(ids):
        return None, False  # déjà tout en haut ou tout en bas
    ranks = cloud_state["ranks"]
    ranks[index], ranks[other] = ranks[other], ranks[index]
    return "Ordre des rangs : " + " < ".join(rank["name"] for rank in ranks), True


def _rank_delete(cloud_state, body, rank_id, ids):
    name = nextcloud_accounts.rank_name(cloud_state, rank_id)
    nextcloud_accounts.delete_rank(commands.occ, cloud_state, rank_id)
    return f"Rang supprimé : {name} (ses membres descendent d'un rang)", True


RANK_ACTIONS = {
    "activer": _rank_switch,
    "creer": _rank_create,
    "renommer": _rank_rename,
    "deplacer": _rank_move,
    "supprimer": _rank_delete,
}


@router.post("/api/nuage/rangs")
async def rank_action(request: Request):
    """Les rangs : réglés par le compte du serveur seulement (c'est lui qui décide de la hiérarchie)."""
    session = guard.require_admin(request, "super")
    if session["username"] != guard.hostname():
        raise HTTPException(403, "Seul le compte du serveur règle les rangs.")
    body = await request.json()
    action = body.get("action")
    if action not in RANK_ACTIONS:
        raise HTTPException(400, "Action inconnue.")
    with nextcloud_accounts._lock:
        cloud_state = nextcloud_accounts.read()
        ids = [rank["id"] for rank in nextcloud_accounts.ranks_of(cloud_state)]
        rank_id = body.get("id")
        if action not in ("activer", "creer") and rank_id not in ids:
            raise HTTPException(404, "Rang inconnu.")
        text, everyone = RANK_ACTIONS[action](cloud_state, body, rank_id, ids)
        if text is None:
            return {"ok": True}
        nextcloud_accounts.write(cloud_state)
    if everyone:
        run_cloud_action(nextcloud_accounts.sync_ranks, cloud_state)
    activity_log.record("nuage", text, request.state.nom)
    return {"ok": True}


# ---------------------------------------------------------------- groupes
@router.post("/api/nuage/groupes")
async def group_action(request: Request):
    guard.require_admin(request)
    body = await request.json()
    name = str(body.get("name", "")).strip()
    if body.get("supprimer"):
        run_cloud_action(nextcloud_accounts.delete_group, name)
        activity_log.record("nuage", f"Groupe supprimé : {name}", request.state.nom)
    else:
        run_cloud_action(nextcloud_accounts.create_group, name)
        activity_log.record("nuage", f"Groupe créé : {name}", request.state.nom)
    return {"ok": True}


# ---------------------------------------------------------------- dossiers communs
def _check_mode(mode, allow_none=False):
    if not (mode in nextcloud_accounts.MODES or (allow_none and mode is None)):
        raise HTTPException(400, "Mode inconnu.")


def _folder_create(body, who):
    name = str(body.get("name", "")).strip()
    group = body.get("group") or nextcloud_accounts.MEMBERS_GROUP
    mode = body.get("mode") or "lecture"
    _check_mode(mode)
    folder_id = run_cloud_action(nextcloud_accounts.create_team_folder, name)
    run_cloud_action(nextcloud_accounts.set_team_folder_access, folder_id, group, mode)
    access = f"{nextcloud_accounts.describe_group(group)}, {nextcloud_accounts.MODES[mode][1].lower()}"
    activity_log.record("nuage", f"Dossier commun créé : {name} ({access})", who)
    return {"ok": True, "id": folder_id}


# Chaque geste sur un dossier qui existe : (folder_id, name, body) -> texte du Journal.
def _folder_access(folder_id, name, body):
    group, mode = str(body.get("group", "")), body.get("mode")
    _check_mode(mode, allow_none=True)
    run_cloud_action(nextcloud_accounts.set_team_folder_access, folder_id, group, mode)
    access = "plus d’accès" if mode is None else nextcloud_accounts.MODES[mode][1].lower()
    return f"Dossier {name} : {nextcloud_accounts.describe_group(group)} → {access}"


def _folder_quota(folder_id, name, body):
    gigabytes = body.get("go")
    if gigabytes is not None and (not isinstance(gigabytes, (int, float)) or gigabytes <= 0):
        raise HTTPException(400, "Taille invalide.")
    run_cloud_action(nextcloud_accounts.set_team_folder_quota, folder_id, gigabytes)
    size = "illimitée" if gigabytes is None else format_gigabytes(gigabytes) + " au plus"
    return f"Dossier {name} : taille {size}"


def _folder_rename(folder_id, name, body):
    run_cloud_action(nextcloud_accounts.rename_team_folder, folder_id, str(body.get("name", "")))
    return f"Dossier {name} renommé en {body.get('name')}"


def _folder_delete(folder_id, name, body):
    run_cloud_action(nextcloud_accounts.delete_team_folder, folder_id)
    return f"Dossier commun supprimé avec son contenu : {name}"


FOLDER_ACTIONS = {
    "acces": _folder_access,
    "quota": _folder_quota,
    "renommer": _folder_rename,
    "supprimer": _folder_delete,
}


@router.post("/api/nuage/dossiers")
async def team_folder_action(request: Request):
    body = await request.json()
    action = body.get("action")
    guard.require_admin(request, strong=action == "supprimer")
    if action == "creer":
        return _folder_create(body, request.state.nom)
    if action not in FOLDER_ACTIONS:
        raise HTTPException(400, "Action inconnue.")
    names = {folder["id"]: folder["name"] for folder in run_cloud_action(nextcloud_accounts.team_folders)}
    folder_id = body.get("id")
    if folder_id not in names:
        raise HTTPException(404, "Dossier inconnu.")
    text = FOLDER_ACTIONS[action](folder_id, names[folder_id], body)
    activity_log.record("nuage", text, request.state.nom)
    return {"ok": True}
