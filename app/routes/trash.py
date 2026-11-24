"""API de la Corbeille : lister, remettre en place, effacer pour de bon (disques et Nextcloud).

Sommaire :
  list_trash          GET  /api/corbeille
  restore_from_trash  POST /api/corbeille/restaurer
  erase_from_trash    POST /api/corbeille/effacer
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import config
from security import guard
from services import activity_log, disk_browser, files_source, nextcloud_files, trash

router = APIRouter()


@router.get("/api/corbeille")
async def list_trash():
    return {"items": await trash.list_entries(), "kept": 30}


@router.post("/api/corbeille/restaurer")
async def restore_from_trash(request: Request):
    body = await request.json()
    path = await trash.restore_entry(str(body.get("id", "")), body.get("source"), body.get("origin"))
    activity_log.record("fichier", f"{path} restauré depuis la corbeille", request.state.nom)
    return {"ok": True, "path": path}


@router.post("/api/corbeille/effacer")
async def erase_from_trash(request: Request):
    """Effacer pour de bon : un élément, ou toute la corbeille. Demande le mot de passe du serveur."""
    guard.require_admin(request, strong=True)
    body = await request.json()
    identifier, source_path, everything = str(body.get("id", "")), body.get("source"), bool(body.get("everything"))
    if config.DEMO and files_source.files_source() != "disque":
        return {"ok": True}
    if everything:
        await asyncio.to_thread(disk_browser.empty_trash)
        if files_source.files_source() == "nextcloud" and config.NC_USER:
            async with nextcloud_files.client() as dav_client:
                await dav_client.delete(nextcloud_files.trash_url())
        activity_log.record("fichier", "Corbeille vidée", request.state.nom)
        return {"ok": True}
    if source_path == "disque":
        files_source.run_disk_action(lambda: disk_browser.erase(identifier))
    else:
        nextcloud_files.require_nextcloud()
        if "/" in identifier:
            raise HTTPException(400, "Élément invalide")
        async with nextcloud_files.client() as dav_client:
            response = await dav_client.delete(nextcloud_files.trash_url(identifier))
        await nextcloud_files.check_response(response, "Effacement")
    activity_log.record("fichier", f"« {body.get('name', identifier)} » effacé définitivement", request.state.nom)
    return {"ok": True}
