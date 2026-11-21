"""Route de « Mes codes » (services/my_codes.py), réservée au super admin. Aucun code n'y est montré.

Sommaire :
  GET /api/codes   codes : la liste des codes, à quoi ils servent, où ils sont rangés
"""

import asyncio

from fastapi import APIRouter, Request

from security import guard
from services import my_codes, tools

router = APIRouter()


@router.get("/api/codes")
async def codes(request: Request):
    guard.require_super_admin(request)
    installed = await asyncio.to_thread(lambda: {tool["id"] for tool in tools.list_tools()})
    return {"codes": my_codes.inventory(installed, request.state.admin)}
