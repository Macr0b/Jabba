"""routes.app_update"""
import json
import os
import time
from services import activity_log
from services import app_update
from fastapi import HTTPException, Request
from version import VERSION
import config
from host import commands
from security import guard
from fastapi import APIRouter

router = APIRouter()

@router.get("/api/appli/maj")
def update_info(verifier: int = 0):
    if config.DEMO:
        return {"actuelle": VERSION, "depot": "exemple/jabba", "pret": True,
                "derniere": {"version": "0.7.0", "tag": "v0.7.0", "notes": "- Correctifs\n- Aperçus plus rapides", "date": "2026-11-20T10:00:00Z"},
                "disponible": True}
    d = app_update.app_folder()
    pret = bool(config.REPOSITORY and d and commands.run_on_host(["test", "-d", d + "/.git"])[0] == 0 and commands.run_on_host(["sh", "-c", "command -v git"])[0] == 0)
    der = app_update.latest_release(config.REPOSITORY, forcer=bool(verifier)) if config.REPOSITORY else None
    return {"actuelle": VERSION, "depot": config.REPOSITORY or None, "pret": pret, "derniere": der,
            "disponible": bool(der and app_update.is_newer(VERSION, der["version"]))}


@router.get("/api/appli/versions")
def version_history():
    """Historique des versions de l'appli : ce qui a changé à chaque mise à jour."""
    try:
        with open(os.path.join(config.APP_DIR, "versions.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


@router.post("/api/appli/maj")
def install_update(request: Request):
    guard.require_admin(request, fort=True)
    if config.DEMO:
        return {"ok": True}
    der = app_update.latest_release(config.REPOSITORY, forcer=True)
    d = app_update.app_folder()
    if not (der and d and app_update.is_newer(VERSION, der["version"])):
        raise HTTPException(409, "Pas de nouvelle version à installer.")
    cle = app_update.publication_key()
    if not cle:
        raise HTTPException(409, "Cette appli n'a pas de clé de publication (cle_publication.pub) : impossible de vérifier une nouvelle version.")
    script = app_update.install_script(d, der["tag"], cle)
    activity_log.record("appli", f"Mise à jour de l'appli vers {der['version']} lancée", request.state.nom)
    # Lancée par le serveur lui-même : elle continue pendant que l'appli redémarre.
    unite = f"jabba-maj-{int(time.time())}"
    code, _, err = commands.start_on_host(unite, script)
    if code != 0:
        raise HTTPException(500, "Lancement impossible : " + err[:200])
    app_update.CURRENT_UPDATE.update(unite=unite, version=der["version"], debut=time.time(), signale=False)
    return {"ok": True, "version": der["version"]}


@router.get("/api/appli/maj/etat")
def update_progress(request: Request):
    guard.require_admin(request)
    if config.DEMO:
        if os.environ.get("JABBA_DEMO_MAJ", "en_cours") == "en_cours":
            t = int(time.time()) % 60
            return {"statut": "en_cours", "version": "0.7.0", "depuis": t,
                    **app_update.update_progress([f"#9 [jabba {1 + t // 10}/6] RUN pip install"] if t > 5 else [])}
        return {"statut": os.environ.get("JABBA_DEMO_MAJ"), "version": "0.7.0",
                "detail": ["erreur : droits insuffisants pour ajouter un objet à la base de données .git/objects du dépôt", "fatal : failed to write object"]}
    return app_update.update_state()
