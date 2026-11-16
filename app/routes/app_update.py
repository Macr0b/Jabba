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
def api_appli_maj(verifier: int = 0):
    if config.DEMO:
        return {"actuelle": VERSION, "depot": "exemple/jabba", "pret": True,
                "derniere": {"version": "0.7.0", "tag": "v0.7.0", "notes": "- Correctifs\n- Aperçus plus rapides", "date": "2026-11-20T10:00:00Z"},
                "disponible": True}
    d = app_update.dossier_appli()
    pret = bool(config.DEPOT and d and commands.hote(["test", "-d", d + "/.git"])[0] == 0 and commands.hote(["sh", "-c", "command -v git"])[0] == 0)
    der = app_update.derniere(config.DEPOT, forcer=bool(verifier)) if config.DEPOT else None
    return {"actuelle": VERSION, "depot": config.DEPOT or None, "pret": pret, "derniere": der,
            "disponible": bool(der and app_update.plus_recente(VERSION, der["version"]))}


@router.get("/api/appli/versions")
def api_appli_versions():
    """Historique des versions de l'appli : ce qui a changé à chaque mise à jour."""
    try:
        with open(os.path.join(config.APP_DIR, "versions.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


@router.post("/api/appli/maj")
def api_appli_installer(request: Request):
    guard.exiger_admin(request, fort=True)
    if config.DEMO:
        return {"ok": True}
    der = app_update.derniere(config.DEPOT, forcer=True)
    d = app_update.dossier_appli()
    if not (der and d and app_update.plus_recente(VERSION, der["version"])):
        raise HTTPException(409, "Pas de nouvelle version à installer.")
    cle = app_update.cle_publication()
    if not cle:
        raise HTTPException(409, "Cette appli n'a pas de clé de publication (cle_publication.pub) : impossible de vérifier une nouvelle version.")
    script = app_update.script(d, der["tag"], cle)
    activity_log.noter("appli", f"Mise à jour de l'appli vers {der['version']} lancée", request.state.nom)
    # Lancée par le serveur lui-même : elle continue pendant que l'appli redémarre.
    unite = f"jabba-maj-{int(time.time())}"
    code, _, err = commands.lancer_sur_serveur(unite, script)
    if code != 0:
        raise HTTPException(500, "Lancement impossible : " + err[:200])
    app_update.MAJ.update(unite=unite, version=der["version"], debut=time.time(), signale=False)
    return {"ok": True, "version": der["version"]}


@router.get("/api/appli/maj/etat")
def api_appli_maj_etat(request: Request):
    guard.exiger_admin(request)
    if config.DEMO:
        if os.environ.get("JABBA_DEMO_MAJ", "en_cours") == "en_cours":
            t = int(time.time()) % 60
            return {"statut": "en_cours", "version": "0.7.0", "depuis": t,
                    **app_update.avancement_maj([f"#9 [jabba {1 + t // 10}/6] RUN pip install"] if t > 5 else [])}
        return {"statut": os.environ.get("JABBA_DEMO_MAJ"), "version": "0.7.0",
                "detail": ["erreur : droits insuffisants pour ajouter un objet à la base de données .git/objects du dépôt", "fatal : failed to write object"]}
    return app_update.etat_maj()
