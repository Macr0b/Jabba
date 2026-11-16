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
def api_sauvegardes(request: Request):
    guard.exiger_admin(request)
    d = backups.suivre_sauvegarde()
    en_cours = backups.sauv_en_cours(d)
    disques = [{"nom": n, "chemin": c} for n, c in sorted(files_source.disques_connus().items())]
    return {
        "actif": d["actif"], "pret": bool(d["chemin"] and backups.phrase()),
        "disque": d["disque"], "chemin": d["chemin"], "branche": backups.sauv_disque_present(d) if d["chemin"] else None,
        "heure": d["heure"], "prochaine": backups.prochaine(d) if d["actif"] else None,
        "sources": [{"id": k, "nom": n, "detail": x, "actif": d["sources"].get(k, True)} for k, (n, x) in backups.SOURCES.items()],
        "garder": backups.GARDER, "en_cours": en_cours, "depuis": d["lancee"] if en_cours else None,
        "dernier": d.get("dernier"), "derniere_reussie": d.get("derniere_reussie"),
        "disques": disques,
    }


@router.post("/api/sauvegardes/activer")
async def api_sauvegardes_activer(request: Request):
    """Met en route les sauvegardes vers un disque : installe restic si besoin et crée la phrase de passe (montrée une fois)."""
    j = guard.exiger_admin(request, "super", fort=True)
    corps = await request.json()
    nom = corps.get("disque")
    chemin = files_source.disques_connus().get(nom)
    if not chemin:
        raise HTTPException(400, "Disque inconnu.")
    d = backups.lire()
    if backups.sauv_en_cours(d):
        raise HTTPException(409, "Une sauvegarde est en cours.")
    if not config.DEMO:
        code, _, _ = commands.hote(["sh", "-c", "command -v restic"])
        if code != 0:
            code, _, err = commands.hote(["apt-get", "install", "-y", "restic"], timeout=600)
            if code != 0:
                raise HTTPException(500, "Installation de restic impossible : " + err[-200:])
    nouvelle = None
    if not backups.phrase():
        nouvelle = backups.nouvelle_phrase()
        backups.poser_phrase(nouvelle)
    d.update(actif=True, disque=nom, chemin=chemin)
    backups.ecrire(d)
    activity_log.noter("sauvegardes", f"Sauvegardes mises en route vers {nom}", request.state.nom)
    return {"ok": True, "phrase": nouvelle, "qui": j["identifiant"]}


@router.post("/api/sauvegardes/reglages")
async def api_sauvegardes_reglages(request: Request):
    guard.exiger_admin(request, "super")
    corps = await request.json()
    d = backups.lire()
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
    backups.ecrire(d)
    if changes:
        activity_log.noter("sauvegardes", "Sauvegardes " + " ; ".join(changes), request.state.nom)
    return {"ok": True}


@router.post("/api/sauvegardes/lancer")
def api_sauvegardes_lancer(request: Request):
    guard.exiger_admin(request)
    backups.lancer_sauvegarde(request.state.nom)
    return {"ok": True}


@router.get("/api/sauvegardes/historique")
def api_sauvegardes_historique(request: Request):
    guard.exiger_admin(request)
    d = backups.lire()
    backups._sauv_lisible(d)
    try:
        return {"copies": backups.instantanes(backups.sauv_executer, d, backups.sauv_donnees_hote(), config.DEMO)}
    except RuntimeError as e:
        if "does not exist" in str(e) or "unable to open config" in str(e):
            return {"copies": []}
        raise HTTPException(500, str(e))


@router.get("/api/sauvegardes/parcourir")
def api_sauvegardes_parcourir(request: Request, id: str, chemin: str = "/"):
    guard.exiger_admin(request)
    if not re.fullmatch(r"[0-9a-f]{8,64}", id):
        raise HTTPException(400, "Copie inconnue.")
    d = backups.lire()
    backups._sauv_lisible(d)
    try:
        return {"elements": backups.parcourir(backups.sauv_executer, d, backups.sauv_donnees_hote(), id, chemin, config.DEMO)}
    except RuntimeError as e:
        raise HTTPException(500, str(e))


@router.post("/api/sauvegardes/restaurer")
async def api_sauvegardes_restaurer(request: Request):
    """Remet un fichier ou un dossier tel qu'il était, à côté (Restaurations), sans rien écraser."""
    guard.exiger_admin(request, fort=True)
    corps = await request.json()
    ident, chemin = str(corps.get("id", "")), str(corps.get("chemin", ""))
    if not re.fullmatch(r"[0-9a-f]{8,64}", ident) or not chemin.strip("/") or ".." in chemin.split("/"):
        raise HTTPException(400, "Demande invalide.")
    d = backups.lire()
    backups._sauv_lisible(d)
    try:
        cible = backups.restaurer(backups.sauv_executer, d, backups.sauv_donnees_hote(), ident, chemin, config.DEMO)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    activity_log.noter("sauvegardes", f"Restauré : {chemin} (copie {ident}) → {cible}", request.state.nom)
    return {"ok": True, "cible": cible}


@router.post("/api/sauvegardes/phrase")
def api_sauvegardes_phrase(request: Request):
    """La phrase de passe, pour la recopier (super admin, mot de passe redemandé, noté au Journal)."""
    guard.exiger_admin(request, "super", fort=True)
    p = backups.phrase()
    if not p:
        raise HTTPException(404, "Pas encore de phrase de passe.")
    activity_log.noter("sauvegardes", "Phrase de passe des sauvegardes affichée", request.state.nom)
    notifications.envoyer("securite", "Phrase des sauvegardes affichée",
                   f"{request.state.nom} a affiché la phrase de passe des sauvegardes.", "/#/journal", famille="admin", noter=False)
    return {"phrase": p}
