"""routes.tools"""
from fastapi import HTTPException, Request
from fastapi.responses import Response
import config
from security import guard
from services import background_tasks
from services import tools
from fastapi import APIRouter

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
    trouve = tools.ICONS_CACHE[pid]
    if not trouve:
        tools.ICONS_CACHE.pop(pid, None)
        raise HTTPException(404)
    return Response(content=trouve[0], media_type=trouve[1], headers={"Cache-Control": "max-age=86400"})


@router.post("/api/outils/{pid}/redemarrer")
def restart_tool(pid: str, request: Request):
    o = tools.find_tool(pid)
    return background_tasks.start_task(f"Redémarrage de {o['nom']}", f"cd '{o['dossier']}' && docker compose restart", request.state.nom, pid)


@router.post("/api/outils/{pid}/demarrer")
def start_tool(pid: str, request: Request):
    guard.require_admin(request)
    o = tools.find_tool(pid)
    script = f"cd '{o['dossier']}' && docker compose up -d"
    if pid in tools.ADRESSES:
        script += f" && (docker start {tools.ADRESSES[pid]} || true)"
    return background_tasks.start_task(f"Activation de {o['nom']}", script, request.state.nom, pid)


@router.post("/api/outils/{pid}/arreter")
def stop_tool(pid: str, request: Request):
    guard.require_admin(request)
    o = tools.find_tool(pid)
    if pid in tools.SYSTEM_TOOLS:
        raise HTTPException(400, "Cet outil fait partie du système.")
    script = f"cd '{o['dossier']}' && docker compose stop"
    if pid in tools.ADRESSES:
        script += f" && (docker stop {tools.ADRESSES[pid]} || true)"
    return background_tasks.start_task(f"Désactivation de {o['nom']}", script, request.state.nom, pid)


@router.post("/api/outils/{pid}/mettre-a-jour")
def update_tool(pid: str, request: Request):
    guard.require_admin(request)
    o = tools.find_tool(pid)
    script = f"cd '{o['dossier']}' && docker compose pull && docker compose up -d && docker image prune -f"
    return background_tasks.start_task(f"Mise à jour de {o['nom']}", script, request.state.nom, pid)
