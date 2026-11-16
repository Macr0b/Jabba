"""API des outils (conteneurs Docker) : liste, icônes, démarrer, arrêter, mettre à jour.

Sommaire :
  GET  /api/outils                       list_tools
  GET  /api/icone/{pid}                  tool_icon
  POST /api/outils/{pid}/redemarrer      restart_tool
  POST /api/outils/{pid}/demarrer        start_tool
  POST /api/outils/{pid}/arreter         stop_tool
  POST /api/outils/{pid}/mettre-a-jour   update_tool
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import config
from security import guard
from services import background_tasks, tools

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


@router.post("/api/outils/{pid}/redemarrer")
def restart_tool(pid: str, request: Request):
    tool = tools.find_tool(pid)
    return background_tasks.start_task(
        f"Redémarrage de {tool['nom']}", f"cd '{tool['dossier']}' && docker compose restart", request.state.nom, pid
    )


@router.post("/api/outils/{pid}/demarrer")
def start_tool(pid: str, request: Request):
    guard.require_admin(request)
    tool = tools.find_tool(pid)
    script = f"cd '{tool['dossier']}' && docker compose up -d"
    if pid in tools.ADRESSES:
        script += f" && (docker start {tools.ADRESSES[pid]} || true)"
    return background_tasks.start_task(f"Activation de {tool['nom']}", script, request.state.nom, pid)


@router.post("/api/outils/{pid}/arreter")
def stop_tool(pid: str, request: Request):
    guard.require_admin(request)
    tool = tools.find_tool(pid)
    if pid in tools.SYSTEM_TOOLS:
        raise HTTPException(400, "Cet outil fait partie du système.")
    script = f"cd '{tool['dossier']}' && docker compose stop"
    if pid in tools.ADRESSES:
        script += f" && (docker stop {tools.ADRESSES[pid]} || true)"
    return background_tasks.start_task(f"Désactivation de {tool['nom']}", script, request.state.nom, pid)


@router.post("/api/outils/{pid}/mettre-a-jour")
def update_tool(pid: str, request: Request):
    guard.require_admin(request)
    tool = tools.find_tool(pid)
    script = f"cd '{tool['dossier']}' && docker compose pull && docker compose up -d && docker image prune -f"
    return background_tasks.start_task(f"Mise à jour de {tool['nom']}", script, request.state.nom, pid)
