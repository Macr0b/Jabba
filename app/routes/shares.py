"""API des dossiers partagés : un dossier (même d'un disque) donné à voir à un rang ou un groupe.

Sommaire :
  GET  /api/partages                 list_shared_folders
  POST /api/partages                 share_folder (mot de passe redemandé)
  POST /api/partages/{sid}/arreter   stop_sharing
  interne : _ocs
"""

from fastapi import APIRouter, HTTPException, Request

import config
from host import commands
from security import guard
from services import activity_log, nextcloud_accounts, nextcloud_files, nextcloud_shares

router = APIRouter()


def _ocs(method, url_path, data):
    return nextcloud_shares.nextcloud_ocs(method, url_path, data)


@router.get("/api/partages")
def list_shared_folders(request: Request):
    guard.require_admin(request)
    return {
        "shares": nextcloud_shares.list_shares(nextcloud_accounts.read()),
        "modes": {key: value["name"] for key, value in nextcloud_shares.MODES.items()},
    }


@router.post("/api/partages")
async def share_folder(request: Request):
    """Partage un dossier de l'écran Fichiers avec un rang ou un groupe."""
    guard.require_admin(request, strong=True)
    body = await request.json()
    path = nextcloud_files.clean_path(str(body.get("path", "")))
    group, mode = str(body.get("group", "")), str(body.get("mode", ""))
    if config.DEMO and not commands.DEMO_OCC:
        raise HTTPException(409, "Nextcloud absent (démo).")
    state = nextcloud_accounts.read()
    try:
        share = nextcloud_shares.share_folder(commands.occ, _ocs, state, path, group, mode)
    except nextcloud_accounts.CloudError as error:
        raise HTTPException(400, str(error))
    nextcloud_accounts.write(state)
    activity_log.record(
        "nuage",
        f"Dossier partagé : {share['path']} avec {nextcloud_accounts.describe_group(group)}"
        f" ({nextcloud_shares.MODES[mode]['name'].lower()})",
        request.state.nom,
    )
    return share


@router.post("/api/partages/{sid}/arreter")
def stop_sharing(sid: str, request: Request):
    guard.require_admin(request)
    state = nextcloud_accounts.read()
    try:
        share = nextcloud_shares.stop_sharing(commands.occ, _ocs, state, sid)
    except nextcloud_accounts.CloudError as error:
        raise HTTPException(400, str(error))
    nextcloud_accounts.write(state)
    activity_log.record(
        "nuage",
        f"Partage arrêté : {share['path']} ({nextcloud_accounts.describe_group(share['group'])})",
        request.state.nom,
    )
    return {"ok": True}
