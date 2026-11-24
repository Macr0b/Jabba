"""Routes des adresses à part des outils (services/tool_addresses.py), réservées au super admin.

Sommaire :
  GET  /api/adresses              addresses_status : la clé est-elle là ? l'adresse de chaque outil
  POST /api/adresses/cle          save_token : garde la clé d'accès Tailscale, puis crée les adresses qui manquent
  POST /api/adresses/creer        create_addresses : crée les adresses qui manquent (clé déjà là)
  POST /api/adresses/oublier-cle  forget_token
  interne : _installed
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from security import guard
from services import activity_log, tool_addresses, tools

router = APIRouter()


def _installed():
    return {tool["id"] for tool in tools.list_tools()}


@router.get("/api/adresses")
async def addresses_status(request: Request):
    guard.require_super_admin(request)
    return tool_addresses.status(await asyncio.to_thread(_installed))


@router.post("/api/adresses/cle")
async def save_token(request: Request):
    guard.require_super_admin(request)
    body = await request.json()
    try:
        await asyncio.to_thread(tool_addresses.save_token, body.get("key"))
        activity_log.record("securite", "Clé d'accès Tailscale donnée à Jabba (adresses des outils)", request.state.nom)
        tool_addresses.create_missing(await asyncio.to_thread(_installed), request.state.nom)
    except tool_addresses.AddressError as error:
        raise HTTPException(400, str(error))
    return tool_addresses.status(await asyncio.to_thread(_installed))


@router.post("/api/adresses/creer")
async def create_addresses(request: Request):
    guard.require_super_admin(request)
    installed = await asyncio.to_thread(_installed)
    try:
        tool_addresses.create_missing(installed, request.state.nom)
    except tool_addresses.AddressError as error:
        raise HTTPException(400, str(error))
    return tool_addresses.status(installed)


@router.post("/api/adresses/oublier-cle")
async def forget_token(request: Request):
    guard.require_super_admin(request)
    tool_addresses.forget_token()
    activity_log.record("securite", "Clé d'accès Tailscale oubliée par Jabba", request.state.nom)
    return tool_addresses.status(await asyncio.to_thread(_installed))
