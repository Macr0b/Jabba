"""Déménager le serveur vers un nouvel ordinateur, sans rien perdre.

Sur l'ancien serveur (Système → Déménager, super admin) :
  1. Jabba arrête les outils (rien ne change plus après la copie), puis copie sur un disque, dans
     `.demenagement-jabba/`, les réglages et les données de tous les outils (/opt/stacks : comptes,
     bases de Nextcloud, Jellyfin, Jabba lui-même…), le compte de Dockge, les tâches planifiées et les
     adresses https du serveur. Les fichiers (/srv/data) aussi, sauf si la copie va sur le disque de
     données lui-même : il suffit alors de le mettre dans le nouvel ordinateur.
  2. « Éteindre l'ancien serveur » libère ses noms dans Tailscale (le nouveau reprend les mêmes
     adresses), puis l'éteint.

Sur le nouveau, le script d'installation trouve le déménagement (disque de données ou disque USB),
demande s'il faut le reprendre, remet tout en place et relance les outils. Jabba refait ensuite tout
seul les adresses à part des outils (services/tool_addresses.py).

Sommaire :
  FOLDER         : le dossier du déménagement sur le disque choisi
  choices        : les disques où copier, et si les fichiers y sont copiés aussi
  move_script    : le script du déménagement (host/scripts/server_move.sh), précédé de ses valeurs
  state          : où en est le déménagement (en cours, réussi, raté et pourquoi)
  start          : lance la copie (refus clair si impossible)
  cancel         : changement d'avis : les outils repartent, la copie est effacée
  finish         : libère les noms Tailscale de l'ancien serveur, puis l'éteint
  interne : _settings, _write_settings, _tailscale_names
"""

import json
import os
import time

import config
from host import commands
from services import activity_log, backups, files_source, json_files, tool_addresses
from services.service_error import ServiceError
from version import VERSION

FOLDER = ".demenagement-jabba"
DATA_DIR = os.environ.get("JABBA_DATA", "/data")
SETTINGS_FILE = os.path.join(DATA_DIR, "demenagement.json")
STATE_FILE = os.path.join(DATA_DIR, "demenagement-etat.json")
# Pourquoi la copie s'est arrêtée (code de sortie du script → phrase).
ERRORS = {
    10: "Le disque n'est plus branché.",
    11: "Le disque ne s'ouvre pas en écriture.",
    12: "Pas assez de place sur le disque.",
    13: "Un outil ne s'est pas arrêté.",
    14: "La copie des outils a échoué.",
    15: "La copie des fichiers a échoué.",
    16: "Ce disque (FAT32) ne garde pas les fichiers de plus de 4 Go : choisis-en un autre.",
}


def _settings():
    return json_files.load(SETTINGS_FILE, {"unit": None, "path": None, "started": 0})


def _write_settings(settings):
    json_files.save(SETTINGS_FILE, settings)


def choices():
    """Le disque de données (s'il est à part : il ira dans le nouvel ordinateur), puis les disques branchés."""
    result = []
    if config.DEMO or files_source.is_mounted("/srv/data"):
        result.append({"name": "Disque de données", "path": "/srv/data", "files": False})
    for name, path in sorted(files_source.known_disks().items()):
        result.append({"name": name, "path": path, "files": True})
    return result


def move_script(path, with_files, data_dir):
    """Le script du déménagement (host/scripts/server_move.sh), précédé de ses valeurs."""
    from host.action_common import script, with_values

    return with_values(
        script("server_move"),
        disk=path,
        copy=os.path.join(path, FOLDER),
        state_file=os.path.join(data_dir, "demenagement-etat.json"),
        with_files=1 if with_files else 0,
        version=VERSION,
    )


def state():
    """Où en est le déménagement : rien, en cours, réussi (outils arrêtés) ou raté (et pourquoi)."""
    settings = _settings()
    running = backups.is_running(settings) if settings.get("unit") else False
    result = json_files.load(STATE_FILE, {})
    finished = bool(settings.get("started")) and result.get("start", 0) >= settings["started"] - 5
    payload = {"disks": choices(), "in_progress": running, "path": settings.get("path")}
    if running or not finished:
        return {**payload, "state": "en cours" if running else "rien"}
    if result.get("code") == 0:
        return {**payload, "state": "pret", "end": result.get("end")}
    message = ERRORS.get(result.get("code"), f"Arrêt à l'étape « {result.get('step')} ».")
    if result.get("code") == 12:
        message += (
            f" Il faut {result.get('needed', 0) // 1024**3 + 1} Go, il en reste {result.get('free', 0) // 1024**3}."
        )
    return {**payload, "state": "rate", "error": message}


def start(path, who=None):
    """Lance la copie vers ce disque (un de ceux de choices()). Les outils s'arrêtent pendant et après."""
    choice = next((disk for disk in choices() if disk["path"] == path), None)
    if not choice:
        raise ServiceError(400, "Choisis un des disques proposés.")
    settings = _settings()
    if settings.get("unit") and backups.is_running(settings):
        raise ServiceError(409, "Le déménagement est déjà en cours.")
    unit = f"jabba-demenage-{int(time.time())}"
    if not config.DEMO:
        code, _, error_output = commands.run_action(
            "demenager", disk=path, files=choice["files"], unit=unit, data=backups.host_data_dir()
        )
        if code != 0:
            raise ServiceError(500, "Lancement impossible : " + error_output[:200])
    _write_settings({"unit": unit, "path": path, "started": int(time.time())})
    activity_log.record("systeme", f"Déménagement du serveur : copie vers {choice['name']}", who)
    return state()


def cancel(who=None):
    """Changement d'avis (ou copie ratée) : les outils arrêtés repartent, et la copie est effacée."""
    settings = _settings()
    if not settings.get("path"):
        raise ServiceError(409, "Pas de déménagement en cours.")
    if settings.get("unit") and backups.is_running(settings):
        raise ServiceError(409, "Attends la fin de la copie.")
    if not config.DEMO:
        commands.run_action("annuler_demenagement", disk=settings["path"])
    _write_settings({"unit": None, "path": None, "started": 0})
    activity_log.record("systeme", "Déménagement annulé : les outils repartent", who)
    return state()


def _tailscale_names():
    """Les noms Tailscale à libérer : les adresses à part des outils, puis le serveur lui-même (en dernier)."""
    names = [url.removeprefix("https://") for url in json_files.load(tool_addresses.ADDRESSES_FILE, {}).values()]
    try:
        server = json.loads(commands.run_action("appareils_tailscale")[1])["Self"]["DNSName"].rstrip(".")
    except (ValueError, KeyError, TypeError):
        server = ""
    return names + ([server] if server else [])


def finish(who=None):
    """Après une copie réussie : libère les noms dans Tailscale (si la clé est là), puis éteint le serveur."""
    if state()["state"] != "pret":
        raise ServiceError(409, "Fais d'abord la copie du déménagement.")
    activity_log.record("systeme", "Déménagement : l'ancien serveur libère ses noms Tailscale et s'éteint", who)
    if config.DEMO:
        return {"ok": True, "freed_names": 0}
    commands.run_action("fin_demenagement")  # dans 30 secondes : déconnexion de Tailscale, puis arrêt
    freed = 0
    try:
        devices = tool_addresses._api("GET", "/tailnet/-/devices")["devices"]
        for name in _tailscale_names():
            for device in devices:
                if device.get("name", "").rstrip(".") == name:
                    tool_addresses._api("DELETE", f"/device/{device['id']}")
                    freed += 1
    except (tool_addresses.AddressError, KeyError, TypeError):
        pass  # sans clé valable, la déconnexion suffit le plus souvent (le guide dit quoi vérifier)
    return {"ok": True, "freed_names": freed}
