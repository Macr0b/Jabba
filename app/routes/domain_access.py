"""API de l'accès par le domaine (outil de l'Établi).

Sommaire :
  GET  /api/domaine          domain_status (la liste de vérification)
  POST /api/domaine          enable_domain (super admin, mot de passe confirmé : ouvre Nextcloud sur Internet)
  POST /api/domaine/retirer  disable_domain (super admin)
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from host import actions
from security import guard
from services import activity_log, domain_access

router = APIRouter()


@router.get("/api/domaine")
async def domain_status(request: Request):
    guard.require_admin(request)
    return await asyncio.to_thread(domain_access.status)


@router.post("/api/domaine")
async def enable_domain(request: Request):
    guard.require_super_admin(request, strong=True)
    body = await request.json()
    try:
        settings = await asyncio.to_thread(domain_access.enable, body.get("domaine"), body.get("prefixe", "cloud"))
    except (ValueError, actions.ActionError) as error:
        raise HTTPException(400, str(error))
    except RuntimeError as error:
        raise HTTPException(500, str(error))
    activity_log.record(
        "securite", f"Accès par le domaine ouvert : {domain_access.address(settings)}", request.state.nom
    )
    return await asyncio.to_thread(domain_access.status, settings)


@router.post("/api/domaine/retirer")
async def disable_domain(request: Request):
    guard.require_super_admin(request)
    settings = await asyncio.to_thread(domain_access.disable)
    activity_log.record("securite", "Accès par le domaine fermé", request.state.nom)
    return await asyncio.to_thread(domain_access.status, settings)
