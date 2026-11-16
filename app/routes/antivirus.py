"""API de l'antivirus (outil de l'Établi).

Sommaire :
  GET  /api/antivirus            antivirus_summary
  POST /api/antivirus/installer  install_antivirus (super admin : télécharge l'image officielle ClamAV)
  POST /api/antivirus/retirer    remove_antivirus (super admin)
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from security import guard
from services import activity_log, antivirus

router = APIRouter()


@router.get("/api/antivirus")
async def antivirus_summary(request: Request):
    guard.require_admin(request)
    return await asyncio.to_thread(antivirus.summary)


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
