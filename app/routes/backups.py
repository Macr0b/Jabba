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
    d = backups.follow_backup()
    en_cours = backups.is_running(d)
    disques = [{"nom": n, "chemin": c} for n, c in sorted(files_source.known_disks().items())]
    return {
        "actif": d["actif"], "pret": bool(d["chemin"] and backups.passphrase()),
        "disque": d["disque"], "chemin": d["chemin"], "branche": backups.disk_is_present(d) if d["chemin"] else None,
        "heure": d["heure"], "prochaine": backups.next_run(d) if d["actif"] else None,
        "sources": [{"id": k, "nom": n, "detail": x, "actif": d["sources"].get(k, True)} for k, (n, x) in backups.SOURCES.items()],
        "garder": backups.KEEP, "en_cours": en_cours, "depuis": d["lancee"] if en_cours else None,
        "dernier": d.get("dernier"), "derniere_reussie": d.get("derniere_reussie"),
        "disques": disques,
    }


@router.post("/api/sauvegardes/activer")
async def enable_backups(request: Request):
    """Met en route les sauvegardes vers un disque : installe restic si besoin et crée la phrase de passe (montrée une fois)."""
    j = guard.require_admin(request, "super", fort=True)
    corps = await request.json()
    nom = corps.get("disque")
    chemin = files_source.known_disks().get(nom)
    if not chemin:
        raise HTTPException(400, "Disque inconnu.")
    d = backups.read()
    if backups.is_running(d):
        raise HTTPException(409, "Une sauvegarde est en cours.")
    if not config.DEMO:
        code, _, _ = commands.run_on_host(["sh", "-c", "command -v restic"])
        if code != 0:
            code, _, err = commands.run_on_host(["apt-get", "install", "-y", "restic"], timeout=600)
            if code != 0:
                raise HTTPException(500, "Installation de restic impossible : " + err[-200:])
    nouvelle = None
    if not backups.passphrase():
        nouvelle = backups.new_passphrase()
        backups.set_passphrase(nouvelle)
    d.update(actif=True, disque=nom, chemin=chemin)
    backups.write(d)
    activity_log.record("sauvegardes", f"Sauvegardes mises en route vers {nom}", request.state.nom)
    return {"ok": True, "phrase": nouvelle, "qui": j["identifiant"]}


@router.post("/api/sauvegardes/reglages")
async def backup_settings(request: Request):
    guard.require_admin(request, "super")
    corps = await request.json()
    d = backups.read()
    changes = []
    if "actif" in corps:
        d["actif"] = bool(corps["actif"]) and bool(d["chemin"])
        changes.append("activées" if d["actif"] else "mises en pause")
    if "heure" in corps:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(corps["heure"])):
            raise HTTPException(400, "Heure invalide.")
        d["heure"] = corps["heure"]
        changes.append(f"chaque nuit à {d['heure']}")
    if isinstance(corps.get("sources"), dict):
        for k, v in corps["sources"].items():
            if k in backups.SOURCES:
                d["sources"][k] = bool(v)
        if not any(d["sources"].values()):
            raise HTTPException(400, "Garde au moins une chose à sauvegarder.")
        changes.append("contenu : " + ", ".join(backups.SOURCES[k][0] for k, v in d["sources"].items() if v))
    backups.write(d)
    if changes:
        activity_log.record("sauvegardes", "Sauvegardes " + " ; ".join(changes), request.state.nom)
    return {"ok": True}


@router.post("/api/sauvegardes/lancer")
def start_backup(request: Request):
    guard.require_admin(request)
    backups.start_backup(request.state.nom)
    return {"ok": True}


@router.get("/api/sauvegardes/historique")
def backup_history(request: Request):
    guard.require_admin(request)
    d = backups.read()
    backups.require_readable(d)
    try:
        return {"copies": backups.snapshots(backups.run_restic, d, backups.host_data_dir(), config.DEMO)}
    except RuntimeError as e:
        if "does not exist" in str(e) or "unable to open config" in str(e):
            return {"copies": []}
        raise HTTPException(500, str(e))


@router.get("/api/sauvegardes/parcourir")
def browse_backup(request: Request, id: str, chemin: str = "/"):
    guard.require_admin(request)
    if not re.fullmatch(r"[0-9a-f]{8,64}", id):
        raise HTTPException(400, "Copie inconnue.")
    d = backups.read()
    backups.require_readable(d)
    try:
        return {"elements": backups.browse(backups.run_restic, d, backups.host_data_dir(), id, chemin, config.DEMO)}
    except RuntimeError as e:
        raise HTTPException(500, str(e))


@router.post("/api/sauvegardes/restaurer")
async def restore_from_backup(request: Request):
    """Remet un fichier ou un dossier tel qu'il était, à côté (Restaurations), sans rien écraser."""
    guard.require_admin(request, fort=True)
    corps = await request.json()
    ident, chemin = str(corps.get("id", "")), str(corps.get("chemin", ""))
    if not re.fullmatch(r"[0-9a-f]{8,64}", ident) or not chemin.strip("/") or ".." in chemin.split("/"):
        raise HTTPException(400, "Demande invalide.")
    d = backups.read()
    backups.require_readable(d)
    try:
        cible = backups.restore(backups.run_restic, d, backups.host_data_dir(), ident, chemin, config.DEMO)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    activity_log.record("sauvegardes", f"Restauré : {chemin} (copie {ident}) → {cible}", request.state.nom)
    return {"ok": True, "cible": cible}


@router.post("/api/sauvegardes/phrase")
def show_passphrase(request: Request):
    """La phrase de passe, pour la recopier (super admin, mot de passe redemandé, noté au Journal)."""
    guard.require_admin(request, "super", fort=True)
    p = backups.passphrase()
    if not p:
        raise HTTPException(404, "Pas encore de phrase de passe.")
    activity_log.record("sauvegardes", "Phrase de passe des sauvegardes affichée", request.state.nom)
    notifications.send("securite", "Phrase des sauvegardes affichée",
                   f"{request.state.nom} a affiché la phrase de passe des sauvegardes.", "/#/journal", famille="admin", noter=False)
    return {"phrase": p}
