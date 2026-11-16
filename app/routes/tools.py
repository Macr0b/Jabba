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
def api_outils():
    return tools.lister_outils()


@router.get("/api/icone/{pid}")
def api_icone(pid: str):
    if pid not in tools.PORTS or config.DEMO:
        raise HTTPException(404)
    if pid not in tools.CACHE_ICONES:
        tools.CACHE_ICONES[pid] = tools.chercher_icone(pid)
    trouve = tools.CACHE_ICONES[pid]
    if not trouve:
        tools.CACHE_ICONES.pop(pid, None)
        raise HTTPException(404)
    return Response(content=trouve[0], media_type=trouve[1], headers={"Cache-Control": "max-age=86400"})


@router.post("/api/outils/{pid}/redemarrer")
def api_redemarrer(pid: str, request: Request):
    o = tools.outil(pid)
    return background_tasks.lancer_tache(f"Redémarrage de {o['nom']}", f"cd '{o['dossier']}' && docker compose restart", request.state.nom, pid)


@router.post("/api/outils/{pid}/demarrer")
def api_demarrer(pid: str, request: Request):
    guard.exiger_admin(request)
    o = tools.outil(pid)
    script = f"cd '{o['dossier']}' && docker compose up -d"
    if pid in tools.ADRESSES:
        script += f" && (docker start {tools.ADRESSES[pid]} || true)"
    return background_tasks.lancer_tache(f"Activation de {o['nom']}", script, request.state.nom, pid)


@router.post("/api/outils/{pid}/arreter")
def api_arreter(pid: str, request: Request):
    guard.exiger_admin(request)
    o = tools.outil(pid)
    if pid in tools.SYSTEME:
        raise HTTPException(400, "Cet outil fait partie du système.")
    script = f"cd '{o['dossier']}' && docker compose stop"
    if pid in tools.ADRESSES:
        script += f" && (docker stop {tools.ADRESSES[pid]} || true)"
    return background_tasks.lancer_tache(f"Désactivation de {o['nom']}", script, request.state.nom, pid)


@router.post("/api/outils/{pid}/mettre-a-jour")
def api_maj(pid: str, request: Request):
    guard.exiger_admin(request)
    o = tools.outil(pid)
    script = f"cd '{o['dossier']}' && docker compose pull && docker compose up -d && docker image prune -f"
    return background_tasks.lancer_tache(f"Mise à jour de {o['nom']}", script, request.state.nom, pid)
