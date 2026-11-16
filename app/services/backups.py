"""Sauvegardes du serveur, avec restic.

Chaque nuit (ou à la demande), une copie chiffrée de ce qui n'existe nulle part
ailleurs part sur un disque choisi : les fichiers (/srv/data, sans les films et
les téléchargements), les réglages des outils (/opt/stacks, avec une copie
propre des bases de données) et ceux du serveur (/etc).

- restic ne garde qu'une fois ce qui ne change pas : chaque sauvegarde ne prend
  que la place des nouveautés ;
- l'historique garde 7 jours, 4 semaines et 6 mois ;
- tout est chiffré avec une phrase de passe, montrée une seule fois à l'admin ;
- la copie est rangée dans un dossier caché du disque, `.sauvegardes-jabba` ;
  un disque en lecture seule n'est ouvert en écriture que le temps de la copie ;
- on ne remplace jamais rien en restaurant : la copie revient dans
  /srv/data/dossiers/Restaurations/<date>/, à côté.

La copie tourne sur le serveur lui-même (systemd-run) : elle va au bout même si
l'appli redémarre. Elle écrit son résultat dans donnees/sauvegarde-etat.json.
"""
import json
import os
import secrets
import shlex
import threading
import time
from datetime import datetime

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
SETTINGS_FILE = os.path.join(DATA_DIR, "sauvegardes.json")
KEY_FILE = os.path.join(DATA_DIR, "sauvegardes.cle")
STATE_FILE = os.path.join(DATA_DIR, "sauvegarde-etat.json")
REPOSITORY_FOLDER = ".sauvegardes-jabba"
KEEP = {"jours": 7, "semaines": 4, "mois": 6}
SOURCES = {
    "fichiers": ("Fichiers", "Tes dossiers sur le serveur (/srv/data), sans les films ni les téléchargements"),
    "outils": ("Réglages des outils", "Nextcloud (avec sa base), Jellyfin, Jabba… (/opt/stacks)"),
    "systeme": ("Réglages du serveur", "Réseau, comptes, disques… (/etc)"),
}
PATHS = {"fichiers": "/srv/data", "outils": "/opt/stacks", "systeme": "/etc"}
EXCLUDE = ["/srv/data/downloads", "/srv/data/media", "/srv/data/dossiers/Restaurations", "/srv/data/.corbeille", "**/transcodes", "**/cache",
           "**/.cache", "**/Cache", "/opt/stacks/*/donnees/sauvegarde-etat.json"]
_lock = threading.RLock()


# ---------------------------------------------------------------- réglages
def read():
    try:
        with open(SETTINGS_FILE) as file:
            settings = json.load(file)
    except (OSError, ValueError):
        settings = {}
    settings.setdefault("actif", False)
    settings.setdefault("disque", None)       # nom du disque choisi
    settings.setdefault("chemin", None)       # point de montage du disque
    settings.setdefault("heure", "03:30")
    settings.setdefault("sources", {key: True for key in SOURCES})
    settings.setdefault("unite", None)        # tâche en cours sur le serveur
    settings.setdefault("lancee", 0)
    settings.setdefault("derniere_reussie", None)
    settings.setdefault("alerte_absent", None)
    return settings


def write(settings):
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        temporary_file = SETTINGS_FILE + ".tmp"
        with open(temporary_file, "w") as file:
            json.dump(settings, file, ensure_ascii=False)
        os.chmod(temporary_file, 0o600)
        os.replace(temporary_file, SETTINGS_FILE)


def new_passphrase():
    """Six groupes de quatre caractères faciles à recopier (sans 0/O ni 1/I/L)."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(6))


def passphrase():
    try:
        with open(KEY_FILE) as file:
            return file.read().strip()
    except OSError:
        return None


def set_passphrase(process):
    with open(KEY_FILE, "w") as file:
        file.write(process + "\n")
    os.chmod(KEY_FILE, 0o600)


def repository(settings):
    return os.path.join(settings["chemin"], REPOSITORY_FOLDER)


# ---------------------------------------------------------------- la copie
def backup_script(settings, donnees_hote, demo=False):
    """Le script shell qui fait une sauvegarde complète, sur le serveur."""
    sources = [PATHS[key] for key, value in settings["sources"].items() if value and key in PATHS]
    if demo:
        sources = [os.environ.get("JABBA_DEMO_SAUVER", "/tmp")]
    excluded = " ".join(f"--exclude {shlex.quote(share)}" for share in EXCLUDE)
    quote = shlex.quote
    return f"""set -uo pipefail
ETAT={quote(os.path.join(donnees_hote, "sauvegarde-etat.json"))}
DISQUE={quote(settings["chemin"])}
export RESTIC_REPOSITORY={quote(repository(settings))}
export RESTIC_PASSWORD_FILE={quote(os.path.join(donnees_hote, "sauvegardes.cle"))}
export RESTIC_CACHE_DIR={quote("/tmp/restic-jabba-cache" if demo else "/var/cache/restic-jabba")}
DEBUT=$(date +%s); ETAPE=disque; REMONTER=0; AVERT=""; RESUME=null
TMP=$(mktemp)
fin() {{
  code=$?
  [ "$REMONTER" = 1 ] && mount -o remount,ro "$DISQUE" 2>/dev/null
  rm -f "$TMP"
  printf '{{"debut":%s,"fin":%s,"code":%s,"etape":"%s","avert":"%s","resume":%s}}\\n' "$DEBUT" "$(date +%s)" "$code" "$ETAPE" "$AVERT" "$RESUME" > "$ETAT"
}}
trap fin EXIT
ls "$DISQUE" >/dev/null 2>&1
{"" if demo else 'mountpoint -q "$DISQUE" || exit 10'}
{"" if demo else 'case ",$(findmnt -no OPTIONS "$DISQUE")," in *,ro,*) mount -o remount,rw "$DISQUE" || exit 11; REMONTER=1;; esac'}
mkdir -p "$RESTIC_REPOSITORY" || exit 12
ETAPE=base
{"" if demo else _database_dump_script()}
ETAPE=init
restic cat config >/dev/null 2>&1 || restic init >/dev/null || exit 13
restic unlock >/dev/null 2>&1
ETAPE=copie
restic backup --tag jabba --exclude-caches {excluded} {" ".join(quote(source_list) for source_list in sources)} --json > "$TMP"
c=$?
RESUME=$(grep '"message_type":"summary"' "$TMP" | tail -1)
[ -n "$RESUME" ] || RESUME=null
if [ $c = 3 ]; then AVERT="${{AVERT}}Quelques fichiers illisibles n'ont pas été copiés. "; elif [ $c != 0 ]; then exit 14; fi
ETAPE=menage
restic forget --tag jabba --keep-daily {KEEP["jours"]} --keep-weekly {KEEP["semaines"]} --keep-monthly {KEEP["mois"]} --prune >/dev/null || AVERT="${{AVERT}}Le tri des anciennes copies a échoué. "
ETAPE=fini
exit 0
"""


def _database_dump_script():
    """Copie propre des bases de données (conteneurs *-db) avant la sauvegarde, dans le dossier de leur outil."""
    return r"""for c in $(docker ps --format '{{.Names}}' | grep -- '-db$'); do
  dir=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$c")
  [ -d "$dir" ] || continue
  img=$(docker inspect -f '{{.Config.Image}}' "$c")
  case "$img" in
    *postgres*) docker exec "$c" sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}"' > "$dir/sauvegarde-base.sql.tmp" ;;
    *mariadb*|*mysql*) docker exec "$c" sh -c 'p="${MARIADB_ROOT_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}"; (command -v mariadb-dump >/dev/null && exec mariadb-dump --all-databases --single-transaction -uroot -p"$p") || exec mysqldump --all-databases --single-transaction -uroot -p"$p"' > "$dir/sauvegarde-base.sql.tmp" ;;
    *) continue ;;
  esac
  if [ -s "$dir/sauvegarde-base.sql.tmp" ]; then mv "$dir/sauvegarde-base.sql.tmp" "$dir/sauvegarde-base.sql"; chmod 600 "$dir/sauvegarde-base.sql"
  else rm -f "$dir/sauvegarde-base.sql.tmp"; AVERT="${AVERT}Base de $c non copiée. "; fi
done"""


def read_state():
    try:
        with open(STATE_FILE) as file:
            return json.loads(file.read() or "{}")
    except (OSError, ValueError):
        return {}


STEPS = {"disque": "Le disque des sauvegardes est introuvable ou ne s'ouvre pas.",
          "base": "La copie des bases de données a échoué.",
          "init": "Le dossier des sauvegardes ne s'ouvre pas (phrase de passe changée ?).",
          "copie": "La copie des fichiers a échoué.",
          "menage": "Le tri des anciennes copies a échoué."}


def result(state):
    """Le dernier résultat, en clair."""
    if not state:
        return None
    r = state.get("resume") or {}
    succeeded = state.get("code") == 0
    return {
        "ok": succeeded,
        "debut": state.get("debut"), "fin": state.get("fin"),
        "duree": max(0, (state.get("fin") or 0) - (state.get("debut") or 0)),
        "erreur": None if succeeded else STEPS.get(state.get("etape"), "La sauvegarde s'est arrêtée."),
        "avert": (state.get("avert") or "").strip() or None,
        "ajoute": r.get("data_added"),
        "fichiers": r.get("total_files_processed"),
        "taille": r.get("total_bytes_processed"),
        "nouveaux": r.get("files_new"), "modifies": r.get("files_changed"),
        "instantane": r.get("snapshot_id"),
    }


def is_due(settings, now=None):
    """Faut-il lancer la sauvegarde de cette nuit ? (manquée : dès que possible)"""
    if not settings["actif"] or not settings["chemin"]:
        return False
    now = now or datetime.now()
    hour, minute = (int(share) for share in settings["heure"].split(":"))
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < scheduled:
        scheduled = scheduled.fromtimestamp(scheduled.timestamp() - 86400)
    if ((settings.get("derniere_reussie") or {}).get("fin") or 0) >= scheduled.timestamp():
        return False
    # Pas encore réussie depuis l'heure prévue : on (re)tente, au plus une fois par heure.
    return now.timestamp() - (settings.get("lancee") or 0) >= 3600


def next_run(settings):
    hour, minute = (int(share) for share in settings["heure"].split(":"))
    now = datetime.now()
    process = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if process <= now:
        process = datetime.fromtimestamp(process.timestamp() + 86400)
    return int(process.timestamp())


# ---------------------------------------------------------------- lire les copies
def restic_command(settings, donnees_hote, demo=False):
    return ["env", f"RESTIC_REPOSITORY={repository(settings)}", f"RESTIC_PASSWORD_FILE={os.path.join(donnees_hote, 'sauvegardes.cle')}",
            f"RESTIC_CACHE_DIR={'/tmp/restic-jabba-cache' if demo else '/var/cache/restic-jabba'}", "restic", "--no-lock"]


def snapshots(run, settings, donnees_hote, demo=False):
    code, output, error_output = run(restic_command(settings, donnees_hote, demo) + ["snapshots", "--tag", "jabba", "--json"], 120)
    if code != 0:
        raise RuntimeError(error_output.strip()[-200:] or "Sauvegardes illisibles.")
    items = json.loads(output or "[]")
    items.sort(key=lambda source_list: source_list["time"], reverse=True)
    return [{"id": source_list["short_id"], "date": source_list["time"][:19], "chemins": source_list.get("paths", []),
             "taille": (source_list.get("summary") or {}).get("total_bytes_processed")} for source_list in items]


def browse(run, settings, donnees_hote, identifier, path, demo=False):
    path = "/" + path.strip("/")
    code, output, error_output = run(restic_command(settings, donnees_hote, demo) + ["ls", "--json", identifier, path], 120)
    if code != 0:
        raise RuntimeError(error_output.strip()[-200:] or "Copie illisible.")
    entries = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("struct_type") != "node" or event.get("path") == path:
            continue
        if os.path.dirname(event["path"].rstrip("/")) != path.rstrip("/") and not (path == "/" and event["path"].count("/") == 1):
            continue
        entries.append({"nom": event["name"], "chemin": event["path"], "dossier": event.get("type") == "dir",
                         "taille": event.get("size"), "modifie": (event.get("mtime") or "")[:19]})
    entries.sort(key=lambda state: (not state["dossier"], state["nom"].lower()))
    return entries


def restore(run, settings, donnees_hote, identifier, path, demo=False):
    """Remet `chemin` tel qu'il était dans la copie `ident`, dans Restaurations/<date>/ (jamais par-dessus)."""
    path = "/" + path.strip("/")
    parent, name = os.path.dirname(path) or "/", os.path.basename(path)
    base = os.environ.get("JABBA_DEMO_RESTAURER", "/srv/data/dossiers/Restaurations") if demo else "/srv/data/dossiers/Restaurations"
    target = os.path.join(base, time.strftime("%Y-%m-%d %Hh%M") + f" ({identifier})")
    code, _, error_output = run(restic_command(settings, donnees_hote, demo) + ["restore", f"{identifier}:{parent}", "--target", target,
                                                                  "--include", "/" + name], 3600)
    if code != 0:
        raise RuntimeError(error_output.strip()[-200:] or "Restauration impossible.")
    if not demo:
        run(["chown", "-R", "1000:1000", base], 600)
    return os.path.join(target, name)


# ---- ajouté au découpage de main.py
import os
import subprocess
import threading
from services import activity_log
from services import app_update
from services import notifications
from fastapi import HTTPException
import config
from host import commands
from services import files_source


# ---------------------------------------------------------------- sauvegardes
DEMO_STATE = {}   # en démo : le processus de la sauvegarde en cours


def run_restic(command, timeout=120):
    """Une commande restic : sur le serveur, ou en démo dans le conteneur."""
    if config.DEMO:
        r = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    return commands.run_on_host(command, timeout)


def host_data_dir():
    """Le dossier des données de l'appli, vu depuis le serveur (la clé et l'état y sont)."""
    if config.DEMO:
        return DATA_DIR
    settings = app_update.app_folder()
    if not settings:
        raise HTTPException(500, "Dossier de l'appli introuvable.")
    return os.path.join(settings, "donnees")


def is_running(settings):
    if not settings["unite"]:
        return False
    if config.DEMO:
        process = DEMO_STATE.get(settings["unite"])
        return bool(process and process.poll() is None)
    _, active, _ = commands.run_on_host(["systemctl", "show", "-p", "ActiveState", "--value", settings["unite"] + ".service"])
    return active.strip() in ("active", "activating")


def disk_is_present(settings):
    return True if config.DEMO else bool(settings["chemin"]) and files_source.disk_is_present(settings["chemin"])


def start_backup(who=None):
    settings = read()
    if not settings["chemin"] or not passphrase():
        raise HTTPException(409, "Les sauvegardes ne sont pas encore mises en route.")
    if is_running(settings):
        raise HTTPException(409, "Une sauvegarde est déjà en cours.")
    if not disk_is_present(settings):
        raise HTTPException(409, f"Le disque {settings['disque']} n'est pas branché.")
    script_content = backup_script(settings, host_data_dir(), config.DEMO)
    unit = f"jabba-sauvegarde-{int(time.time())}"
    if config.DEMO:
        DEMO_STATE[unit] = subprocess.Popen(["bash", "-c", script_content])
    else:
        # Lancée par le serveur lui-même, en douceur (le serveur reste réactif) :
        # elle va au bout même si l'appli redémarre.
        code, _, error_output = commands.start_on_host(unit, script_content, "bash", ("Nice=10", "IOSchedulingClass=idle"))
        if code != 0:
            raise HTTPException(500, "Lancement impossible : " + error_output[:200])
    settings.update(unite=unit, lancee=int(time.time()))
    write(settings)
    activity_log.record("sauvegardes", f"Sauvegarde lancée vers {settings['disque']}" + ("" if who else " (automatique)"), who)
    return unit


def follow_backup():
    """Quand la sauvegarde en cours est finie : on note le résultat, et on prévient si ça a raté."""
    settings = read()
    if not settings["unite"] or is_running(settings):
        return settings
    state = read_state()
    res = result(state) if (state.get("debut") or 0) >= settings["lancee"] - 5 else None
    if res is None:
        res = {"ok": False, "debut": settings["lancee"], "fin": int(time.time()), "duree": 0,
               "erreur": "La sauvegarde s'est arrêtée avant la fin (serveur redémarré ?)."}
    settings["unite"] = None
    settings["dernier"] = res
    if res["ok"]:
        settings["derniere_reussie"] = res
        settings["alerte_absent"] = None
        activity_log.record("sauvegardes", "Sauvegarde terminée" + (f" ({res['avert'].strip()})" if res.get("avert") else ""), None)
        if res.get("avert"):
            notifications.send("sauvegardes", "Sauvegarde incomplète", res["avert"], "/#/sauvegardes", family="sauvegardes", log=False)
    else:
        # Une alerte par jour au plus (la sauvegarde est retentée chaque heure).
        day = time.strftime("%Y-%m-%d")
        if settings.get("alerte_echec") != day:
            notifications.send("sauvegardes", "Sauvegarde échouée", res["erreur"] + (" Nouvel essai dans une heure." if settings["actif"] else ""),
                           "/#/sauvegardes", family="sauvegardes")
        else:
            activity_log.record("sauvegardes", "Sauvegarde échouée — " + res["erreur"])
        settings["alerte_echec"] = day
    write(settings)
    return settings


def watch_backups():
    settings = follow_backup()
    if settings["unite"] or not is_due(settings):
        return
    if disk_is_present(settings):
        start_backup()
        return
    # Disque absent : on prévient une fois par jour, et la copie part dès qu'il revient.
    day = time.strftime("%Y-%m-%d")
    if settings.get("alerte_absent") != day:
        settings["alerte_absent"] = day
        write(settings)
        notifications.send("sauvegardes", "Sauvegarde en attente",
                       f"Le disque {settings['disque']} n'est pas branché : la sauvegarde partira dès son retour.",
                       "/#/sauvegardes", family="sauvegardes")


def require_readable(settings):
    if not settings["chemin"] or not passphrase():
        raise HTTPException(409, "Les sauvegardes ne sont pas encore mises en route.")
    if not disk_is_present(settings):
        raise HTTPException(409, f"Branche le disque {settings['disque']} pour voir les sauvegardes.")
