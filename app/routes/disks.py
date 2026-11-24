"""API des disques branchés au serveur : liste, lecture seule ou écriture, éjection.

Sommaire :
  GET  /api/disques                 list_disks
  POST /api/disques/{nom}/mode      set_disk_mode
  POST /api/disques/{nom}/ejecter   eject_disk
  POST /api/disques/{nom}/oublier   forget_disk
  POST /api/disques/reprendre       resume_disk
"""

import re
import threading
import time

from fastapi import APIRouter, HTTPException, Request

import config
from host import actions, commands
from security import guard
from services import activity_log, auto_disks, disk_inventory, files_source

router = APIRouter()


# ---------------------------------------------------------------- disques
@router.get("/api/disques")
def list_disks():
    return disk_inventory.list_disks()


@router.post("/api/disques/{nom}/mode")
async def set_disk_mode(nom: str, request: Request):
    guard.require_admin(request)
    path = files_source.disk_path(nom)
    writable = bool((await request.json()).get("ecriture"))
    if config.DEMO:
        return {"ok": True, "ecriture": writable}
    try:
        code, _, error_output = commands.run_action("mode_disque", chemin=path, ecriture=writable)
    except actions.ActionError as error:
        raise HTTPException(400, str(error))
    if code == 4:
        raise HTTPException(404, "Ce disque n'est pas déclaré sur le serveur.")
    if code != 0:
        raise HTTPException(500, "Impossible d'enregistrer le réglage : " + error_output[:200])
    # Même réglage côté Nextcloud, qui relit aussi le disque : il garde en mémoire les droits de
    # chaque fichier, et refuserait sinon de supprimer ou de renommer (« lecture seule ? »).
    threading.Thread(target=files_source.rescan_in_nextcloud, args=(path,), daemon=True).start()
    activity_log.record("disque", f"{nom} : {'écriture autorisée' if writable else 'lecture seule'}", request.state.nom)
    return {"ok": True, "ecriture": writable}


@router.post("/api/disques/{nom}/ejecter")
def eject_disk(nom: str, request: Request):
    path = files_source.disk_path(nom)
    if config.DEMO:
        files_source.EJECTED[nom] = time.time()
        return {"ok": True}
    code, _, error_output = commands.run_action("ejecter", chemin=path)
    if code != 0 and "not mounted" not in error_output:
        raise HTTPException(
            409,
            "Le disque est en cours d'utilisation (lecture d'un film, copie…). Arrête ce qui l'utilise et réessaie.",
        )
    files_source.EJECTED[nom] = time.time()
    activity_log.record("disque", f"{nom} éjecté", request.state.nom)
    return {"ok": True}


@router.post("/api/disques/{nom}/oublier")
def forget_disk(nom: str, request: Request):
    guard.require_admin(request, strong=True)
    if config.DEMO:
        return {"ok": True}
    try:
        disk = auto_disks.forget(nom)
    except KeyError:
        raise HTTPException(404, "Seuls les disques ajoutés automatiquement peuvent être oubliés ici.")
    except (RuntimeError, actions.ActionError) as error:
        raise HTTPException(500, "Le serveur n'a pas pu oublier ce disque : " + str(error)[:200])
    activity_log.record("disque", f"{disk['nom']} oublié : il ne sera plus ajouté tout seul", request.state.nom)
    return {"ok": True}


@router.post("/api/disques/reprendre")
async def resume_disk(request: Request):
    guard.require_admin(request)
    disk_uuid = str((await request.json()).get("uuid", ""))
    if not re.fullmatch(r"[\w-]{4,64}", disk_uuid):
        raise HTTPException(400, "Disque invalide")
    if not config.DEMO:
        auto_disks.resume(disk_uuid)
        threading.Thread(target=auto_disks.scan_disks, daemon=True).start()
    activity_log.record("disque", "Disque remis en ajout automatique", request.state.nom)
    return {"ok": True}
