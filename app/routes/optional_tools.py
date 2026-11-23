"""API des outils en option de l'Établi et de la mémoire du serveur.

Sommaire :
  GET  /api/etabli/catalogue            optional_catalog (outils en option + mémoire)
  POST /api/etabli/{outil}/installer    install_optional (super admin ; rien à taper)
  POST /api/etabli/{outil}/retirer      remove_optional (super admin ; données gardées)
  GET  /api/etabli/{outil}/identifiants tool_login (super admin ; le compte créé par Jabba, ou noté par toi)
  PUT  /api/etabli/{outil}/identifiants save_tool_login (super admin ; noter l'identifiant et le mot de passe)
  DELETE /api/etabli/{outil}/identifiants forget_tool_login (super admin)
  GET  /api/rangement/{ecran}           cards_order (admin) : l'ordre et les cartes masquées (« Ranger »)
  PUT  /api/rangement/{ecran}           save_cards_order (admin)
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from host import actions
from security import guard
from services import activity_log, card_order, optional_tools, tool_credentials, tool_usage, tools

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
        if tool_credentials.created_by_jabba(outil):
            raise HTTPException(
                404,
                "Pas de compte créé par Jabba : l'outil a été installé avant que Jabba s'en charge, avec le mot de passe choisi à ce moment-là.",
            )
        raise HTTPException(404, "Rien de noté pour l'instant.")
    activity_log.record("outil", f"Identifiants de {outil} affichés", request.state.nom)
    return login


@router.put("/api/etabli/{outil}/identifiants")
async def save_tool_login(outil: str, request: Request):
    guard.require_super_admin(request)
    body = await request.json()
    try:
        tool_credentials.save(outil, body.get("identifiant"), body.get("mot_de_passe"))
    except ValueError as error:
        raise HTTPException(400, str(error))
    activity_log.record("outil", f"Identifiants de {outil} notés dans Jabba", request.state.nom)
    return tool_credentials.read(outil)


@router.delete("/api/etabli/{outil}/identifiants")
def forget_tool_login(outil: str, request: Request):
    guard.require_super_admin(request)
    try:
        tool_credentials.forget(outil)
    except ValueError as error:
        raise HTTPException(400, str(error))
    activity_log.record("outil", f"Identifiants de {outil} oubliés par Jabba", request.state.nom)
    return {"ok": True}


@router.get("/api/rangement/{ecran}")
def cards_order(ecran: str, request: Request):
    guard.require_admin(request)
    try:
        return card_order.read(ecran)
    except ValueError as error:
        raise HTTPException(404, str(error))


@router.put("/api/rangement/{ecran}")
async def save_cards_order(ecran: str, request: Request):
    guard.require_admin(request)
    body = await request.json()
    try:
        return card_order.save(ecran, body.get("ordre"), body.get("masques"))
    except ValueError as error:
        raise HTTPException(400, str(error))
