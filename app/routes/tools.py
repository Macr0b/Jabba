"""API des outils (conteneurs Docker) : liste, icônes, démarrer, arrêter, mettre à jour.

Sommaire :
  GET  /api/outils                       list_tools
  GET  /api/icone/{pid}                  tool_icon
  POST /api/outils/{pid}/redemarrer      restart_tool
  POST /api/outils/{pid}/demarrer        start_tool
  POST /api/outils/{pid}/arreter         stop_tool
  POST /api/outils/{pid}/mettre-a-jour   update_tool
  GET  /api/outils-conso                 usage_per_tool (mémoire et processeur de chaque outil)
  GET  /api/outils-versions              tool_versions (mise à jour disponible ?)
  POST /api/outils-versions/verifier     check_tool_versions
  interne : _tool_task
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import config
from host import actions
from security import guard
from services import background_tasks, tool_updates, tool_usage, tools

router = APIRouter()


@router.get("/api/outils")
def list_tools():
    return tools.list_tools()


@router.get("/api/icone/{pid}")
def tool_icon(pid: str):
    if pid not in tools.PORTS or config.DEMO:
        raise HTTPException(404)
    if pid not in tools.ICONS_CACHE:
        tools.ICONS_CACHE[pid] = tools.find_icon(pid)
    found = tools.ICONS_CACHE[pid]
    if not found:
        tools.ICONS_CACHE.pop(pid, None)
        raise HTTPException(404)
    return Response(content=found[0], media_type=found[1], headers={"Cache-Control": "max-age=86400"})


TOOL_TASKS = {
    "redemarrer": "Redémarrage de {}",
    "demarrer": "Activation de {}",
    "arreter": "Désactivation de {}",
    "mettre_a_jour": "Mise à jour de {}",
}


def _tool_task(request, pid, gesture):
    """Un geste sur un outil, par la liste fermée (action « outil »), suivi comme tâche longue."""
    guard.require_admin(request)
    tool = tools.find_tool(pid)
    args = {"folder": tool["folder"], "gesture": gesture, "address": tools.ADRESSES.get(pid, "")}
    try:
        actions.build("outil", args)  # refusé tout de suite si le dossier n'est pas celui d'un outil
    except actions.ActionError as error:
        raise HTTPException(400, str(error))
    return background_tasks.start_task(TOOL_TASKS[gesture].format(tool["name"]), "outil", args, request.state.nom, pid)


@router.post("/api/outils/{pid}/redemarrer")
def restart_tool(pid: str, request: Request):
    return _tool_task(request, pid, "redemarrer")


@router.post("/api/outils/{pid}/demarrer")
def start_tool(pid: str, request: Request):
    return _tool_task(request, pid, "demarrer")


@router.post("/api/outils/{pid}/arreter")
def stop_tool(pid: str, request: Request):
    if pid in tools.SYSTEM_TOOLS:
        raise HTTPException(400, "Cet outil fait partie du système.")
    return _tool_task(request, pid, "arreter")


@router.post("/api/outils/{pid}/mettre-a-jour")
def update_tool(pid: str, request: Request):
    tool_updates.forget(pid)
    return _tool_task(request, pid, "mettre_a_jour")


@router.get("/api/outils-conso")
async def usage_per_tool(request: Request):
    guard.require_admin(request)
    return await asyncio.to_thread(tool_usage.read_usage)


@router.get("/api/outils-versions")
def tool_versions(request: Request):
    guard.require_admin(request)
    return tool_updates.summary()


@router.post("/api/outils-versions/verifier")
async def check_tool_versions(request: Request):
    guard.require_admin(request)
    if config.DEMO:
        return tool_updates.summary()
    return await asyncio.to_thread(tool_updates.check_all)
