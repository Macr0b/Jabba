"""API de la santé des disques (lecture SMART).

Sommaire :
  GET  /api/disques/sante            health_summary
  POST /api/disques/sante/activer    enable_health_checks (installe smartmontools : clic d'un super admin)
  POST /api/disques/sante/verifier   check_now
  GET  /api/disques/entretien        maintenance_summary (TRIM du SSD, fragmentation)
  POST /api/disques/entretien/trim   enable_trim (super admin)
  POST /api/disques/entretien/mesurer measure_fragmentation
  POST /api/disques/entretien/defragmenter defragment_disk
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import config
from host import actions, commands
from security import guard
from services import activity_log, disk_health, disk_maintenance

router = APIRouter()


@router.get("/api/disques/sante")
def health_summary(request: Request):
    guard.require_admin(request)
    return disk_health.summary()


@router.post("/api/disques/sante/activer")
async def enable_health_checks(request: Request):
    guard.require_admin(request, "super", strong=True)
    if not config.DEMO and not disk_health.installed():
        code, _, error_output = await asyncio.to_thread(
            commands.run_on_host, ["apt-get", "install", "-y", "smartmontools"], 600
        )
        if code != 0:
            raise HTTPException(500, "Installation de smartmontools impossible : " + error_output[-200:])
    activity_log.record("disque", "Surveillance de la santé des disques activée", request.state.nom)
    await asyncio.to_thread(disk_health.check_all)
    return disk_health.summary()


@router.post("/api/disques/sante/verifier")
async def check_now(request: Request):
    guard.require_admin(request)
    if not disk_health.installed():
        raise HTTPException(409, "Active d'abord la surveillance.")
    await asyncio.to_thread(disk_health.check_all)
    return disk_health.summary()


@router.get("/api/disques/entretien")
async def maintenance_summary(request: Request):
    guard.require_admin(request)
    return await asyncio.to_thread(disk_maintenance.summary)


@router.post("/api/disques/entretien/trim")
async def enable_trim(request: Request):
    guard.require_admin(request, "super", strong=True)
    if not await asyncio.to_thread(disk_maintenance.enable_trim):
        raise HTTPException(500, "Activation du TRIM impossible.")
    activity_log.record("disque", "TRIM hebdomadaire du SSD activé", request.state.nom)
    return await asyncio.to_thread(disk_maintenance.summary)


@router.post("/api/disques/entretien/mesurer")
def measure_fragmentation(request: Request):
    guard.require_admin(request)
    if not disk_maintenance.start_measure():
        raise HTTPException(409, "Une mesure ou une défragmentation est déjà en cours.")
    return {"ok": True}


@router.post("/api/disques/entretien/defragmenter")
async def defragment_disk(request: Request):
    guard.require_admin(request, strong=True)
    path = str((await request.json()).get("chemin", ""))
    known = {disk["chemin"] for disk in disk_maintenance.summary()["disques"]}
    if path not in known:
        raise HTTPException(400, "Disque inconnu ou pas en ext4 à plateaux.")
    try:
        started = disk_maintenance.start_defragment(path, request.state.nom)
    except actions.ActionError as error:
        raise HTTPException(400, str(error))
    if not started:
        raise HTTPException(409, "Une mesure ou une défragmentation est déjà en cours.")
    activity_log.record("disque", f"Défragmentation de {path} lancée", request.state.nom)
    return {"ok": True}
