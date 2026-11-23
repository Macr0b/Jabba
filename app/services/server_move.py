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
  move_script    : le script shell du déménagement, écrit ici à partir du disque choisi
  state          : où en est le déménagement (en cours, réussi, raté et pourquoi)
  start          : lance la copie (refus clair si impossible)
  cancel         : changement d'avis : les outils repartent, la copie est effacée
  finish         : libère les noms Tailscale de l'ancien serveur, puis l'éteint
  interne : _settings, _write_settings, _tailscale_names
"""

import json
import os
import shlex
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
    return json_files.load(SETTINGS_FILE, {"unite": None, "chemin": None, "lance": 0})


def _write_settings(settings):
    json_files.save(SETTINGS_FILE, settings)


def choices():
    """Le disque de données (s'il est à part : il ira dans le nouvel ordinateur), puis les disques branchés."""
    result = []
    if config.DEMO or files_source.is_mounted("/srv/data"):
        result.append({"nom": "Disque de données", "chemin": "/srv/data", "fichiers": False})
    for name, path in sorted(files_source.known_disks().items()):
        result.append({"nom": name, "chemin": path, "fichiers": True})
    return result


def move_script(path, with_files, data_dir):
    """Arrête les outils, copie tout dans <disque>/.demenagement-jabba, et relance les outils si ça rate."""
    quote = shlex.quote
    folder = quote(os.path.join(path, FOLDER))
    return f"""set -uo pipefail
DISQUE={quote(path)}; DOSSIER={folder}; ETAT={quote(os.path.join(data_dir, "demenagement-etat.json"))}
DEBUT=$(date +%s); ETAPE=disque; REMONTER=0; BESOIN=0; LIBRE=0
fin() {{
  code=$?
  if [ "$code" != 0 ] && [ -f "$DOSSIER.tmp/outils-arretes" ]; then
    while read -r d; do docker compose --project-directory "$d" start >/dev/null 2>&1; done < "$DOSSIER.tmp/outils-arretes"
  fi
  [ "$code" = 0 ] || rm -rf "$DOSSIER.tmp"
  [ "$REMONTER" = 1 ] && mount -o remount,ro "$DISQUE" 2>/dev/null
  printf '{{"debut":%s,"fin":%s,"code":%s,"etape":"%s","besoin":%s,"libre":%s}}\\n' \\
    "$DEBUT" "$(date +%s)" "$code" "$ETAPE" "$BESOIN" "$LIBRE" > "$ETAT"
}}
trap fin EXIT
mountpoint -q "$DISQUE" || exit 10
[ "$(findmnt -no FSTYPE "$DISQUE")" = vfat ] && exit 16
case ",$(findmnt -no OPTIONS "$DISQUE")," in *,ro,*) mount -o remount,rw "$DISQUE" || exit 11; REMONTER=1;; esac
ETAPE=place
rm -rf "$DOSSIER" "$DOSSIER.tmp"
BESOIN=$(du -sbx --exclude='adresse-*' --exclude=adresses /opt/stacks /opt/dockge/data 2>/dev/null | awk '{{s+=$1}} END {{print s+0}}')
{"" if not with_files else "BESOIN=$((BESOIN + $(du -sbx --exclude=Restaurations /srv/data | cut -f1)))"}
LIBRE=$(df -B1 --output=avail "$DISQUE" | tail -1 | tr -d ' ')
[ "$BESOIN" -lt "$LIBRE" ] || exit 12
mkdir -p "$DOSSIER.tmp" || exit 11
ETAPE=arret
for d in $(docker ps --format '{{{{.Label "com.docker.compose.project.working_dir"}}}}' | sort -u); do
  case "$d" in /opt/stacks/jabba-app|/opt/dockge|"") continue;; esac
  echo "$d" >> "$DOSSIER.tmp/outils-arretes"
  docker compose --project-directory "$d" stop >/dev/null 2>&1 || exit 13
done
ETAPE=outils
QUOI=stacks; [ -d /opt/dockge/data ] && QUOI="$QUOI dockge/data"
tar -C /opt --numeric-owner --xattrs --acls --one-file-system \\
  --exclude='stacks/adresse-*' --exclude=stacks/adresses -cpf "$DOSSIER.tmp/outils.tar" $QUOI
[ $? -le 1 ] || exit 14
crontab -l > "$DOSSIER.tmp/crontab" 2>/dev/null || true
tailscale serve status --json > "$DOSSIER.tmp/tailscale-serve.json" 2>/dev/null || true
{
        ""
        if not with_files
        else '''ETAPE=fichiers
tar -C /srv --numeric-owner --xattrs --acls --one-file-system --exclude='data/.demenagement-jabba*' \\
  --exclude=data/dossiers/Restaurations -cpf "$DOSSIER.tmp/fichiers.tar" data
[ $? -le 1 ] || exit 15'''
    }
printf '{{"version":"%s","date":%s,"serveur":"%s","compte":"%s","fichiers":%s}}\\n' \\
  {quote(VERSION)} "$DEBUT" "$(hostname)" "$(getent passwd 1000 | cut -d: -f1)" {"true" if with_files else "false"} \\
  > "$DOSSIER.tmp/manifeste.json"
mv "$DOSSIER.tmp" "$DOSSIER" && sync
ETAPE=fini
"""


def state():
    """Où en est le déménagement : rien, en cours, réussi (outils arrêtés) ou raté (et pourquoi)."""
    settings = _settings()
    running = backups.is_running(settings) if settings.get("unite") else False
    result = json_files.load(STATE_FILE, {})
    finished = bool(settings.get("lance")) and result.get("debut", 0) >= settings["lance"] - 5
    payload = {"disques": choices(), "en_cours": running, "chemin": settings.get("chemin")}
    if running or not finished:
        return {**payload, "etat": "en cours" if running else "rien"}
    if result.get("code") == 0:
        return {**payload, "etat": "pret", "fin": result.get("fin")}
    message = ERRORS.get(result.get("code"), f"Arrêt à l'étape « {result.get('etape')} ».")
    if result.get("code") == 12:
        message += (
            f" Il faut {result.get('besoin', 0) // 1024**3 + 1} Go, il en reste {result.get('libre', 0) // 1024**3}."
        )
    return {**payload, "etat": "rate", "erreur": message}


def start(path, who=None):
    """Lance la copie vers ce disque (un de ceux de choices()). Les outils s'arrêtent pendant et après."""
    choice = next((disk for disk in choices() if disk["chemin"] == path), None)
    if not choice:
        raise ServiceError(400, "Choisis un des disques proposés.")
    settings = _settings()
    if settings.get("unite") and backups.is_running(settings):
        raise ServiceError(409, "Le déménagement est déjà en cours.")
    unit = f"jabba-demenage-{int(time.time())}"
    if not config.DEMO:
        code, _, error_output = commands.run_action(
            "demenager", disque=path, fichiers=choice["fichiers"], unite=unit, donnees=backups.host_data_dir()
        )
        if code != 0:
            raise ServiceError(500, "Lancement impossible : " + error_output[:200])
    _write_settings({"unite": unit, "chemin": path, "lance": int(time.time())})
    activity_log.record("systeme", f"Déménagement du serveur : copie vers {choice['nom']}", who)
    return state()


def cancel(who=None):
    """Changement d'avis (ou copie ratée) : les outils arrêtés repartent, et la copie est effacée."""
    settings = _settings()
    if not settings.get("chemin"):
        raise ServiceError(409, "Pas de déménagement en cours.")
    if settings.get("unite") and backups.is_running(settings):
        raise ServiceError(409, "Attends la fin de la copie.")
    if not config.DEMO:
        commands.run_action("annuler_demenagement", disque=settings["chemin"])
    _write_settings({"unite": None, "chemin": None, "lance": 0})
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
    if state()["etat"] != "pret":
        raise ServiceError(409, "Fais d'abord la copie du déménagement.")
    activity_log.record("systeme", "Déménagement : l'ancien serveur libère ses noms Tailscale et s'éteint", who)
    if config.DEMO:
        return {"ok": True, "noms_liberes": 0}
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
    return {"ok": True, "noms_liberes": freed}
