"""Routes du déménagement du serveur (services/server_move.py), réservées au super admin.

Sommaire :
  GET  /api/demenagement            move_state : les disques possibles, et où en est la copie
  POST /api/demenagement/lancer     start_move : copie vers le disque choisi (mot de passe redemandé)
  POST /api/demenagement/annuler    cancel_move : les outils repartent, la copie est effacée
  POST /api/demenagement/terminer   finish_move : libère les noms Tailscale, puis éteint l'ancien serveur
"""

import asyncio

from fastapi import APIRouter, Request

from security import guard
from services import server_move

router = APIRouter()


@router.get("/api/demenagement")
async def move_state(request: Request):
    guard.require_super_admin(request)
    return await asyncio.to_thread(server_move.state)


@router.post("/api/demenagement/lancer")
async def start_move(request: Request):
    guard.require_admin(request, "super", strong=True)
    body = await request.json()
    return await asyncio.to_thread(server_move.start, str(body.get("path") or ""), request.state.nom)


@router.post("/api/demenagement/terminer")
async def finish_move(request: Request):
    guard.require_admin(request, "super", strong=True)
    return await asyncio.to_thread(server_move.finish, request.state.nom)


@router.post("/api/demenagement/annuler")
async def cancel_move(request: Request):
    guard.require_super_admin(request)
    return await asyncio.to_thread(server_move.cancel, request.state.nom)
