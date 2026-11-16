"""routes.backups"""

import re
from services import backups
from services import activity_log
from services import notifications
from fastapi import HTTPException, Request
import config
from host import commands
from security import guard
from services import files_source
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/sauvegardes")
def backup_state(request: Request):
    guard.require_admin(request)
    settings = backups.follow_backup()
    running = backups.is_running(settings)
    disks = [{"nom": name, "chemin": disk_path} for name, disk_path in sorted(files_source.known_disks().items())]
    return {
        "actif": settings["actif"],
        "pret": bool(settings["chemin"] and backups.passphrase()),
        "disque": settings["disque"],
        "chemin": settings["chemin"],
        "branche": backups.disk_is_present(settings) if settings["chemin"] else None,
        "heure": settings["heure"],
        "prochaine": backups.next_run(settings) if settings["actif"] else None,
        "sources": [
            {"id": key, "nom": name, "detail": label, "actif": settings["sources"].get(key, True)}
            for key, (name, label) in backups.SOURCES.items()
        ],
        "garder": backups.KEEP,
        "en_cours": running,
        "depuis": settings["lancee"] if running else None,
        "dernier": settings.get("dernier"),
        "derniere_reussie": settings.get("derniere_reussie"),
        "disques": disks,
    }


@router.post("/api/sauvegardes/activer")
async def enable_backups(request: Request):
    """Met en route les sauvegardes vers un disque : installe restic si besoin et crée la phrase de passe (montrée une fois)."""
    session = guard.require_admin(request, "super", strong=True)
    body = await request.json()
    name = body.get("disque")
    path = files_source.known_disks().get(name)
    if not path:
        raise HTTPException(400, "Disque inconnu.")
    settings = backups.read()
    if backups.is_running(settings):
        raise HTTPException(409, "Une sauvegarde est en cours.")
    if not config.DEMO:
        code, _, _ = commands.run_on_host(["sh", "-c", "command -v restic"])
        if code != 0:
            code, _, error_output = commands.run_on_host(["apt-get", "install", "-y", "restic"], timeout=600)
            if code != 0:
                raise HTTPException(500, "Installation de restic impossible : " + error_output[-200:])
    new_value = None
    if not backups.passphrase():
        new_value = backups.new_passphrase()
        backups.set_passphrase(new_value)
    settings.update(actif=True, disque=name, chemin=path)
    backups.write(settings)
    activity_log.record("sauvegardes", f"Sauvegardes mises en route vers {name}", request.state.nom)
    return {"ok": True, "phrase": new_value, "qui": session["identifiant"]}


@router.post("/api/sauvegardes/reglages")
async def backup_settings(request: Request):
    guard.require_admin(request, "super")
    body = await request.json()
    settings = backups.read()
    changed = []
    if "actif" in body:
        settings["actif"] = bool(body["actif"]) and bool(settings["chemin"])
        changed.append("activées" if settings["actif"] else "mises en pause")
    if "heure" in body:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(body["heure"])):
            raise HTTPException(400, "Heure invalide.")
        settings["heure"] = body["heure"]
        changed.append(f"chaque nuit à {settings['heure']}")
    if isinstance(body.get("sources"), dict):
        for key, value in body["sources"].items():
            if key in backups.SOURCES:
                settings["sources"][key] = bool(value)
        if not any(settings["sources"].values()):
            raise HTTPException(400, "Garde au moins une chose à sauvegarder.")
        changed.append(
            "contenu : " + ", ".join(backups.SOURCES[key][0] for key, value in settings["sources"].items() if value)
        )
    backups.write(settings)
    if changed:
        activity_log.record("sauvegardes", "Sauvegardes " + " ; ".join(changed), request.state.nom)
    return {"ok": True}


@router.post("/api/sauvegardes/lancer")
def start_backup(request: Request):
    guard.require_admin(request)
    backups.start_backup(request.state.nom)
    return {"ok": True}


@router.get("/api/sauvegardes/historique")
def backup_history(request: Request):
    guard.require_admin(request)
    settings = backups.read()
    backups.require_readable(settings)
    try:
        return {"copies": backups.snapshots(backups.run_restic, settings, backups.host_data_dir(), config.DEMO)}
    except RuntimeError as e:
        if "does not exist" in str(e) or "unable to open config" in str(e):
            return {"copies": []}
        raise HTTPException(500, str(e))


@router.get("/api/sauvegardes/parcourir")
def browse_backup(request: Request, id: str, chemin: str = "/"):
    guard.require_admin(request)
    if not re.fullmatch(r"[0-9a-f]{8,64}", id):
        raise HTTPException(400, "Copie inconnue.")
    settings = backups.read()
    backups.require_readable(settings)
    try:
        return {
            "elements": backups.browse(backups.run_restic, settings, backups.host_data_dir(), id, chemin, config.DEMO)
        }
    except RuntimeError as e:
        raise HTTPException(500, str(e))


@router.post("/api/sauvegardes/restaurer")
async def restore_from_backup(request: Request):
    """Remet un fichier ou un dossier tel qu'il était, à côté (Restaurations), sans rien écraser."""
    guard.require_admin(request, strong=True)
    body = await request.json()
    identifier, path = str(body.get("id", "")), str(body.get("chemin", ""))
    if not re.fullmatch(r"[0-9a-f]{8,64}", identifier) or not path.strip("/") or ".." in path.split("/"):
        raise HTTPException(400, "Demande invalide.")
    settings = backups.read()
    backups.require_readable(settings)
    try:
        target = backups.restore(backups.run_restic, settings, backups.host_data_dir(), identifier, path, config.DEMO)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    activity_log.record("sauvegardes", f"Restauré : {path} (copie {identifier}) → {target}", request.state.nom)
    return {"ok": True, "cible": target}


@router.post("/api/sauvegardes/phrase")
def show_passphrase(request: Request):
    """La phrase de passe, pour la recopier (super admin, mot de passe redemandé, noté au Journal)."""
    guard.require_admin(request, "super", strong=True)
    phrase = backups.passphrase()
    if not phrase:
        raise HTTPException(404, "Pas encore de phrase de passe.")
    activity_log.record("sauvegardes", "Phrase de passe des sauvegardes affichée", request.state.nom)
    notifications.send(
        "securite",
        "Phrase des sauvegardes affichée",
        f"{request.state.nom} a affiché la phrase de passe des sauvegardes.",
        "/#/journal",
        family="admin",
        log=False,
    )
    return {"phrase": phrase}
