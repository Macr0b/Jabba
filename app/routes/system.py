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
def task_state(tid: str):
    if tid not in background_tasks.TASKS:
        raise HTTPException(404, "Tâche inconnue")
    return background_tasks.TASKS[tid]


# ---------------------------------------------------------------- accueil / système
@router.get("/api/moi")
def who_am_i(request: Request):
    serveur = "serveur-demo" if config.DEMO else commands.run_on_host(["hostname"])[1].strip()
    j = request.state.session
    return {"login": request.state.user, "nom": request.state.nom, "version": VERSION, "serveur": serveur,
            "remise": accounts.pending_reset(request.state.user),
            "mode": "admin" if j and not j.get("provisoire") else "invite",
            "admin": {"identifiant": j["identifiant"], "rang": j["rang"], "provisoire": j.get("provisoire", False)} if j else None}


@router.get("/api/etat")
def home_state():
    if config.DEMO:
        from services.demo_data import HOME_STATE
        return HOME_STATE
    files_source.wake_disks()
    disques = measurements.disk_space(["/", "/srv/data"])
    outils = tools.list_tools()
    code, _, _ = commands.run_on_host(["test", "-f", "/var/run/reboot-required"])
    soucis = [o["nom"] for o in outils if o["etat"] in ("arrêté", "en panne", "partiel") and o["id"] != "jabba-app"]
    return {
        "cpu": measurements.cpu_percent(),
        "memoire": measurements.memory(),
        "temperature": measurements.temperature(),
        "systeme": disques.get("/"),
        "donnees": disques.get("/srv/data"),
        "duree": measurements.uptime(),
        "redemarrage_requis": code == 0,
        "redemarrage_auto": restart_policy.current_mode()["mode"] == "auto" if code == 0 else None,
        "outils_actifs": sum(1 for o in outils if o["etat"] == "actif"),
        "outils_total": len(outils),
        "soucis": soucis,
    }


@router.get("/api/systeme")
def system_state():
    if config.DEMO:
        from services.demo_data import SYSTEM
        return SYSTEM
    _, nom, _ = commands.run_on_host(["hostname"])
    _, osr, _ = commands.run_on_host(["cat", "/etc/os-release"])
    jolie = re.search(r'PRETTY_NAME="([^"]+)"', osr)
    _, noyau, _ = commands.run_on_host(["uname", "-r"])
    code, _, _ = commands.run_on_host(["test", "-f", "/var/run/reboot-required"])
    _, journal, _ = commands.run_shell_on_host("tail -n 200 /var/log/unattended-upgrades/unattended-upgrades.log 2>/dev/null")
    derniere = None
    for ligne in journal.splitlines():
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d)", ligne)
        if m:
            derniere = m.group(1)
    appareils = []
    c, ts, _ = commands.run_on_host(["tailscale", "status", "--json"], timeout=10)
    if c == 0:
        try:
            d = json.loads(ts)
            for p in (d.get("Peer") or {}).values():
                appareils.append({"nom": p.get("HostName"), "os": p.get("OS"), "en_ligne": p.get("Online", False)})
        except ValueError:
            pass
    return {
        "nom": nom.strip(), "os": jolie.group(1) if jolie else "", "noyau": noyau.strip(),
        "duree": measurements.uptime(), "redemarrage_requis": code == 0,
        "redemarrage": restart_policy.current_mode(),
        "derniere_maj_auto": derniere, "appareils": sorted(appareils, key=lambda a: (not a["en_ligne"], a["nom"] or "")),
        "version_appli": VERSION, "courant": power.POWER_STATE, "conso": power.read_consumption(),
    }


@router.get("/api/systeme/redemarrage")
def restart_policy_state(request: Request):
    guard.require_admin(request)
    return restart_policy.current_mode()


@router.post("/api/systeme/redemarrage")
async def set_restart_policy(request: Request):
    """Choisit : redémarrage automatique à 4 h, ou à la main. Super admin, mot de passe récent."""
    guard.require_super_admin(request, fort=True)
    mode = str((await request.json()).get("mode") or "")
    if mode not in restart_policy.MODES:
        raise HTTPException(400, "Choix inconnu.")
    if config.DEMO:
        with open(restart_policy.DEMO_FILE, "w") as f:
            f.write(mode + "\n")
    else:
        code, _, err = commands.run_shell_on_host(restart_policy.apply_script(mode), timeout=60)
        if code != 0:
            raise HTTPException(500, err.strip()[-200:] or "Échec du réglage.")
    activity_log.record("systeme", "Redémarrage après une mise à jour : " + ("automatique à 4 h" if mode == "auto" else "à la main"), request.state.nom)
    return {"ok": True}


@router.post("/api/systeme/redemarrer")
def reboot_server(request: Request):
    guard.require_admin(request, fort=True)
    if config.DEMO:
        return {"ok": True}
    activity_log.record("systeme", "Redémarrage du serveur demandé", request.state.nom)
    # « redemarrer » (posé par le script d'installation) note le redémarrage au journal du serveur.
    commands.run_on_host(["systemd-run", "--on-active=3", "sh", "-c", "[ -x /usr/local/sbin/redemarrer ] && exec /usr/local/sbin/redemarrer; exec systemctl reboot"])
    return {"ok": True}


# ---------------------------------------------------------------- journal d'activité
@router.get("/api/journal")
def activity(n: int = 100, famille: str = ""):
    res = activity_log.read(min(max(n, 1), 500), famille or None)
    if config.DEMO:
        from services.demo_data import ACTIVITY
        res += [e for e in ACTIVITY if not famille or e["famille"] == famille]
    return res
