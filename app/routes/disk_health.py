"""API de la santé des disques (lecture SMART).

Sommaire :
  GET  /api/disques/sante            health_summary
  POST /api/disques/sante/activer    enable_health_checks (installe smartmontools : clic d'un super admin)
  POST /api/disques/sante/verifier   check_now
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

import config
from host import commands
from security import guard
from services import activity_log, disk_health

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
