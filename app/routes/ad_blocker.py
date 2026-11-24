"""API du bloqueur de pubs (AdGuard Home), appareil par appareil.

Sommaire :
  GET  /api/adguard/appareils    list_devices (admin) : les appareils vus par AdGuard, filtrés ou non
  POST /api/adguard/appareils    protect_device (admin) : {ip, protege} allume ou éteint le filtrage
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from security import guard
from services import activity_log, ad_blocker

router = APIRouter()


@router.get("/api/adguard/appareils")
async def list_devices(request: Request):
    guard.require_admin(request)
    return await asyncio.to_thread(ad_blocker.devices)


@router.post("/api/adguard/appareils")
async def protect_device(request: Request):
    guard.require_admin(request)
    body = await request.json()
    protected = bool(body.get("protected"))
    try:
        await asyncio.to_thread(ad_blocker.set_protected, body.get("ip"), protected)
    except ad_blocker.AdBlockerError as error:
        raise HTTPException(400, str(error))
    state = "allumé" if protected else "éteint"
    activity_log.record("outil", f"Bloqueur de pubs {state} pour {body.get('ip')}", request.state.nom)
    return {"ok": True}
