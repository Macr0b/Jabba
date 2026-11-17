"""API de l'antivirus (outil de l'Établi).

Sommaire :
  GET  /api/antivirus            antivirus_summary
  POST /api/antivirus/installer  install_antivirus (super admin : télécharge l'image officielle ClamAV)
  POST /api/antivirus/retirer    remove_antivirus (super admin)
  POST /api/antivirus/disques    weekly_disk_scan (cocher un disque pour l'analyse de la semaine)
  POST /api/antivirus/analyser   scan_disk_now (analyse complète d'un disque, tout de suite)
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from security import guard
from services import activity_log, antivirus, antivirus_disks

router = APIRouter()


@router.get("/api/antivirus")
async def antivirus_summary(request: Request):
    guard.require_admin(request)
    summary = await asyncio.to_thread(antivirus.summary)
    summary["disques"] = await asyncio.to_thread(lambda: antivirus_disks.disks(antivirus.read_state()))
    return summary


@router.post("/api/antivirus/installer")
def install_antivirus(request: Request):
    guard.require_admin(request, "super", strong=True)
    if not antivirus.install():
        raise HTTPException(409, "L'installation est déjà en cours.")
    activity_log.record("outil", "Installation de l'antivirus lancée", request.state.nom)
    return {"ok": True}


@router.post("/api/antivirus/retirer")
async def remove_antivirus(request: Request):
    guard.require_admin(request, "super", strong=True)
    if not await asyncio.to_thread(antivirus.remove):
        raise HTTPException(500, "Impossible de retirer l'antivirus.")
    activity_log.record("outil", "Antivirus retiré", request.state.nom)
    return {"ok": True}


def _disk_path(body):
    path = str(body.get("chemin") or "")
    if path not in antivirus_disks.available_disks():
        raise HTTPException(404, "Disque inconnu.")
    return path


@router.post("/api/antivirus/disques")
async def weekly_disk_scan(request: Request):
    guard.require_admin(request)
    body = await request.json()
    path = _disk_path(body)
    enabled = body.get("chaque_semaine") is True
    await asyncio.to_thread(antivirus_disks.set_weekly, path, enabled)
    activity_log.record(
        "outil", f"Antivirus : {path} {'analysé' if enabled else 'plus analysé'} chaque semaine", request.state.nom
    )
    return {"ok": True}


@router.post("/api/antivirus/analyser")
async def scan_disk_now(request: Request):
    guard.require_admin(request)
    path = _disk_path(await request.json())
    if not await asyncio.to_thread(antivirus.installed):
        raise HTTPException(409, "L'antivirus n'est pas installé.")
    if not await asyncio.to_thread(antivirus_disks.start_scan, path):
        raise HTTPException(409, "Une analyse de disque est déjà en cours.")
    activity_log.record("outil", f"Antivirus : analyse de {path} lancée", request.state.nom)
    return {"ok": True}
