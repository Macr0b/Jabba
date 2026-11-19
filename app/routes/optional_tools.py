"""API des outils en option de l'Établi et de la mémoire du serveur.

Sommaire :
  GET  /api/etabli/catalogue            optional_catalog (outils en option + mémoire)
  POST /api/etabli/{outil}/installer    install_optional (super admin ; rien à taper)
  POST /api/etabli/{outil}/retirer      remove_optional (super admin ; données gardées)
  GET  /api/etabli/{outil}/identifiants tool_login (super admin ; le compte que Jabba a créé dans l'outil)
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from host import actions
from security import guard
from services import activity_log, optional_tools, tool_credentials, tool_usage, tools

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
    try:
        return optional_tools.install(outil, request.state.nom)
    except actions.ActionError as error:
        raise HTTPException(400, str(error))


@router.post("/api/etabli/{outil}/retirer")
async def remove_optional(outil: str, request: Request):
    guard.require_super_admin(request)
    try:
        return optional_tools.remove(outil, request.state.nom)
    except actions.ActionError as error:
        raise HTTPException(400, str(error))


@router.get("/api/etabli/{outil}/identifiants")
def tool_login(outil: str, request: Request):
    guard.require_super_admin(request)
    login = tool_credentials.read(outil)
    if not login:
        raise HTTPException(404, "Cet outil n'a pas de compte créé par Jabba.")
    activity_log.record("outil", f"Identifiants de {outil} affichés", request.state.nom)
    return login
