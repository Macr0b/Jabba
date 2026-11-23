"""Routes de la surveillance extérieure (services/heartbeat.py), réservées au super admin.

Sommaire :
  GET  /api/surveillance          heartbeat_state : en place ?, dernier signal (jamais l'adresse)
  POST /api/surveillance          save_heartbeat : essaie l'adresse de signal, puis la garde
  POST /api/surveillance/oublier  forget_heartbeat
"""

import asyncio

from fastapi import APIRouter, Request

from security import guard
from services import activity_log, heartbeat

router = APIRouter()


@router.get("/api/surveillance")
async def heartbeat_state(request: Request):
    guard.require_super_admin(request)
    return heartbeat.status()


@router.post("/api/surveillance")
async def save_heartbeat(request: Request):
    guard.require_super_admin(request)
    body = await request.json()
    result = await asyncio.to_thread(heartbeat.save, body.get("url"))
    activity_log.record("systeme", "Surveillance extérieure en place (signal toutes les 5 minutes)", request.state.nom)
    return result


@router.post("/api/surveillance/oublier")
async def forget_heartbeat(request: Request):
    guard.require_super_admin(request)
    activity_log.record("systeme", "Surveillance extérieure retirée", request.state.nom)
    return heartbeat.forget()
