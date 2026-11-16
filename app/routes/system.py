"""API de l'accueil et de l'écran Système : état, tâches, redémarrage, journal.

Sommaire :
  GET  /api/taches/{tid}            task_state
  GET  /api/moi                     who_am_i
  GET  /api/etat                    home_state
  GET  /api/systeme                 system_state
  GET  /api/systeme/redemarrage     restart_policy_state
  POST /api/systeme/redemarrage     set_restart_policy
  POST /api/systeme/redemarrer      reboot_server
  GET  /api/journal                 activity
"""

import json
import re

from fastapi import APIRouter, HTTPException, Request

import config
from host import commands
from security import accounts, guard
from services import activity_log, background_tasks, files_source, measurements, power, restart_policy, tools
from version import VERSION

router = APIRouter()


@router.get("/api/taches/{tid}")
def task_state(tid: str):
    if tid not in background_tasks.TASKS:
        raise HTTPException(404, "Tâche inconnue")
    return background_tasks.TASKS[tid]


# ---------------------------------------------------------------- accueil / système
@router.get("/api/moi")
def who_am_i(request: Request):
    server = "serveur-demo" if config.DEMO else commands.run_on_host(["hostname"])[1].strip()
    session = request.state.session
    return {
        "login": request.state.user,
        "nom": request.state.nom,
        "version": VERSION,
        "serveur": server,
        "remise": accounts.pending_reset(request.state.user),
        "mode": "admin" if session and not session.get("provisoire") else "invite",
        "admin": {
            "identifiant": session["identifiant"],
            "rang": session["rang"],
            "provisoire": session.get("provisoire", False),
        }
        if session
        else None,
    }


@router.get("/api/etat")
def home_state():
    if config.DEMO:
        from services.demo_data import HOME_STATE

        return HOME_STATE
    files_source.wake_disks()
    disks = measurements.disk_space(["/", "/srv/data"])
    tools_list = tools.list_tools()
    code, _, _ = commands.run_on_host(["test", "-f", "/var/run/reboot-required"])
    problems = [
        tool["nom"]
        for tool in tools_list
        if tool["etat"] in ("arrêté", "en panne", "partiel") and tool["id"] != "jabba-app"
    ]
    return {
        "cpu": measurements.cpu_percent(),
        "memoire": measurements.memory(),
        "temperature": measurements.temperature(),
        "systeme": disks.get("/"),
        "donnees": disks.get("/srv/data"),
        "duree": measurements.uptime(),
        "redemarrage_requis": code == 0,
        "redemarrage_auto": restart_policy.current_mode()["mode"] == "auto" if code == 0 else None,
        "outils_actifs": sum(1 for tool in tools_list if tool["etat"] == "actif"),
        "outils_total": len(tools_list),
        "soucis": problems,
    }


@router.get("/api/systeme")
def system_state():
    if config.DEMO:
        from services.demo_data import SYSTEM

        return SYSTEM
    _, name, _ = commands.run_on_host(["hostname"])
    _, os_release, _ = commands.run_on_host(["cat", "/etc/os-release"])
    pretty = re.search(r'PRETTY_NAME="([^"]+)"', os_release)
    _, kernel, _ = commands.run_on_host(["uname", "-r"])
    code, _, _ = commands.run_on_host(["test", "-f", "/var/run/reboot-required"])
    _, log_text, _ = commands.run_shell_on_host(
        "tail -n 200 /var/log/unattended-upgrades/unattended-upgrades.log 2>/dev/null"
    )
    latest = None
    for line in log_text.splitlines():
        match = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d)", line)
        if match:
            latest = match.group(1)
    devices = []
    status, tailscale_output, _ = commands.run_on_host(["tailscale", "status", "--json"], timeout=10)
    if status == 0:
        try:
            status_data = json.loads(tailscale_output)
            for peer in (status_data.get("Peer") or {}).values():
                devices.append(
                    {"nom": peer.get("HostName"), "os": peer.get("OS"), "en_ligne": peer.get("Online", False)}
                )
        except ValueError:
            pass
    return {
        "nom": name.strip(),
        "os": pretty.group(1) if pretty else "",
        "noyau": kernel.strip(),
        "duree": measurements.uptime(),
        "redemarrage_requis": code == 0,
        "redemarrage": restart_policy.current_mode(),
        "derniere_maj_auto": latest,
        "appareils": sorted(devices, key=lambda account: (not account["en_ligne"], account["nom"] or "")),
        "version_appli": VERSION,
        "courant": power.POWER_STATE,
        "conso": power.read_consumption(),
    }


@router.get("/api/systeme/redemarrage")
def restart_policy_state(request: Request):
    guard.require_admin(request)
    return restart_policy.current_mode()


@router.post("/api/systeme/redemarrage")
async def set_restart_policy(request: Request):
    """Choisit : redémarrage automatique à 4 h, ou à la main. Super admin, mot de passe récent."""
    guard.require_super_admin(request, strong=True)
    mode = str((await request.json()).get("mode") or "")
    if mode not in restart_policy.MODES:
        raise HTTPException(400, "Choix inconnu.")
    if config.DEMO:
        with open(restart_policy.DEMO_FILE, "w") as file:
            file.write(mode + "\n")
    else:
        code, _, error_output = commands.run_shell_on_host(restart_policy.apply_script(mode), timeout=60)
        if code != 0:
            raise HTTPException(500, error_output.strip()[-200:] or "Échec du réglage.")
    activity_log.record(
        "systeme",
        "Redémarrage après une mise à jour : " + ("automatique à 4 h" if mode == "auto" else "à la main"),
        request.state.nom,
    )
    return {"ok": True}


@router.post("/api/systeme/redemarrer")
def reboot_server(request: Request):
    guard.require_admin(request, strong=True)
    if config.DEMO:
        return {"ok": True}
    activity_log.record("systeme", "Redémarrage du serveur demandé", request.state.nom)
    # « redemarrer » (posé par le script d'installation) note le redémarrage au journal du serveur.
    commands.run_on_host(
        [
            "systemd-run",
            "--on-active=3",
            "sh",
            "-c",
            "[ -x /usr/local/sbin/redemarrer ] && exec /usr/local/sbin/redemarrer; exec systemctl reboot",
        ]
    )
    return {"ok": True}


# ---------------------------------------------------------------- journal d'activité
@router.get("/api/journal")
def activity(n: int = 100, famille: str = ""):
    result = activity_log.read(min(max(n, 1), 500), famille or None)
    if config.DEMO:
        from services.demo_data import ACTIVITY

        result += [events for events in ACTIVITY if not famille or events["famille"] == famille]
    return result
