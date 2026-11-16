"""routes.system"""
import json
import re
from services import restart_policy
from security import accounts
from services import activity_log
from fastapi import HTTPException, Request
from version import VERSION
import config
from host import commands
from security import guard
from services import background_tasks
from services import files_source
from services import measurements
from services import power
from services import tools
from fastapi import APIRouter

router = APIRouter()

@router.get("/api/taches/{tid}")
def tache(tid: str):
    if tid not in background_tasks.TACHES:
        raise HTTPException(404, "Tâche inconnue")
    return background_tasks.TACHES[tid]


# ---------------------------------------------------------------- accueil / système
@router.get("/api/moi")
def api_moi(request: Request):
    serveur = "serveur-demo" if config.DEMO else commands.hote(["hostname"])[1].strip()
    j = request.state.session
    return {"login": request.state.user, "nom": request.state.nom, "version": VERSION, "serveur": serveur,
            "remise": accounts.remise_en_attente(request.state.user),
            "mode": "admin" if j and not j.get("provisoire") else "invite",
            "admin": {"identifiant": j["identifiant"], "rang": j["rang"], "provisoire": j.get("provisoire", False)} if j else None}


@router.get("/api/etat")
def api_etat():
    if config.DEMO:
        from services.demo_data import ETAT
        return ETAT
    files_source.reveiller_disques()
    disques = measurements.espace(["/", "/srv/data"])
    outils = tools.lister_outils()
    code, _, _ = commands.hote(["test", "-f", "/var/run/reboot-required"])
    soucis = [o["nom"] for o in outils if o["etat"] in ("arrêté", "en panne", "partiel") and o["id"] != "jabba-app"]
    return {
        "cpu": measurements.cpu_pourcent(),
        "memoire": measurements.memoire(),
        "temperature": measurements.temperature(),
        "systeme": disques.get("/"),
        "donnees": disques.get("/srv/data"),
        "duree": measurements.duree_marche(),
        "redemarrage_requis": code == 0,
        "redemarrage_auto": restart_policy.mode_redemarrage()["mode"] == "auto" if code == 0 else None,
        "outils_actifs": sum(1 for o in outils if o["etat"] == "actif"),
        "outils_total": len(outils),
        "soucis": soucis,
    }


@router.get("/api/systeme")
def api_systeme():
    if config.DEMO:
        from services.demo_data import SYSTEME
        return SYSTEME
    _, nom, _ = commands.hote(["hostname"])
    _, osr, _ = commands.hote(["cat", "/etc/os-release"])
    jolie = re.search(r'PRETTY_NAME="([^"]+)"', osr)
    _, noyau, _ = commands.hote(["uname", "-r"])
    code, _, _ = commands.hote(["test", "-f", "/var/run/reboot-required"])
    _, journal, _ = commands.hote_sh("tail -n 200 /var/log/unattended-upgrades/unattended-upgrades.log 2>/dev/null")
    derniere = None
    for ligne in journal.splitlines():
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d)", ligne)
        if m:
            derniere = m.group(1)
    appareils = []
    c, ts, _ = commands.hote(["tailscale", "status", "--json"], timeout=10)
    if c == 0:
        try:
            d = json.loads(ts)
            for p in (d.get("Peer") or {}).values():
                appareils.append({"nom": p.get("HostName"), "os": p.get("OS"), "en_ligne": p.get("Online", False)})
        except ValueError:
            pass
    return {
        "nom": nom.strip(), "os": jolie.group(1) if jolie else "", "noyau": noyau.strip(),
        "duree": measurements.duree_marche(), "redemarrage_requis": code == 0,
        "redemarrage": restart_policy.mode_redemarrage(),
        "derniere_maj_auto": derniere, "appareils": sorted(appareils, key=lambda a: (not a["en_ligne"], a["nom"] or "")),
        "version_appli": VERSION, "courant": power.COURANT, "conso": power.lire_conso(),
    }


@router.get("/api/systeme/redemarrage")
def api_redemarrage(request: Request):
    guard.exiger_admin(request)
    return restart_policy.mode_redemarrage()


@router.post("/api/systeme/redemarrage")
async def api_redemarrage_regler(request: Request):
    """Choisit : redémarrage automatique à 4 h, ou à la main. Super admin, mot de passe récent."""
    guard.exiger_super(request, fort=True)
    mode = str((await request.json()).get("mode") or "")
    if mode not in restart_policy.MODES:
        raise HTTPException(400, "Choix inconnu.")
    if config.DEMO:
        with open(restart_policy.REDEM_DEMO, "w") as f:
            f.write(mode + "\n")
    else:
        code, _, err = commands.hote_sh(restart_policy.script(mode), timeout=60)
        if code != 0:
            raise HTTPException(500, err.strip()[-200:] or "Échec du réglage.")
    activity_log.noter("systeme", "Redémarrage après une mise à jour : " + ("automatique à 4 h" if mode == "auto" else "à la main"), request.state.nom)
    return {"ok": True}


@router.post("/api/systeme/redemarrer")
def api_reboot(request: Request):
    guard.exiger_admin(request, fort=True)
    if config.DEMO:
        return {"ok": True}
    activity_log.noter("systeme", "Redémarrage du serveur demandé", request.state.nom)
    # « redemarrer » (posé par le script d'installation) note le redémarrage au journal du serveur.
    commands.hote(["systemd-run", "--on-active=3", "sh", "-c", "[ -x /usr/local/sbin/redemarrer ] && exec /usr/local/sbin/redemarrer; exec systemctl reboot"])
    return {"ok": True}


# ---------------------------------------------------------------- journal d'activité
@router.get("/api/journal")
def api_journal(n: int = 100, famille: str = ""):
    res = activity_log.lire(min(max(n, 1), 500), famille or None)
    if config.DEMO:
        from services.demo_data import JOURNAL
        res += [e for e in JOURNAL if not famille or e["famille"] == famille]
    return res
