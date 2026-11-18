"""API des outils en option de l'Établi et de la mémoire du serveur.

Sommaire :
  GET  /api/etabli/catalogue            optional_catalog (outils en option + mémoire)
  POST /api/etabli/{outil}/installer    install_optional (super admin ; mot de passe pour Time Machine)
  POST /api/etabli/{outil}/retirer      remove_optional (super admin ; données gardées)
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from host import actions
from security import guard
from services import optional_tools, tool_usage, tools

router = APIRouter()


@router.get("/api/etabli/catalogue")
async def optional_catalog(request: Request):
    guard.require_admin(request)
    installed = {tool["id"] for tool in await asyncio.to_thread(tools.list_tools)}
    usage = await asyncio.to_thread(tool_usage.read_usage)
    return {
        "outils": optional_tools.catalog(installed),
        "memoire": await asyncio.to_thread(optional_tools.memory_budget, usage),
    }


@router.post("/api/etabli/{outil}/installer")
async def install_optional(outil: str, request: Request):
    guard.require_super_admin(request)
    body = await request.json()
    try:
        return optional_tools.install(outil, body.get("mot_de_passe"), request.state.nom)
    except actions.ActionError as error:
        raise HTTPException(400, str(error))


@router.post("/api/etabli/{outil}/retirer")
async def remove_optional(outil: str, request: Request):
    guard.require_super_admin(request)
    try:
        return optional_tools.remove(outil, request.state.nom)
    except actions.ActionError as error:
        raise HTTPException(400, str(error))
