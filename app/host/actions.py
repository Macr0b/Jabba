"""La liste fermée des actions que l'appli peut demander au serveur.

Chaque action a un nom et des paramètres vérifiés ; la commande est construite ICI, jamais
reçue de l'extérieur. Le service privilégié (host/host_service.py) n'exécute que ce qui
figure dans cette liste : une faille dans l'interface web ne peut pas faire lancer
n'importe quelle commande sur le serveur (recommandation ANSSI : moindre privilège).

Sommaire
- ActionError                 : paramètre refusé.
- build(name, args)           : (commande, délai) pour une action connue, sinon ActionError.
- check_input(name, text)     : seules certaines actions reçoivent un texte sur l'entrée standard (un mot de passe).
- host_input(name, args, text): ce qui part vraiment vers la commande (rien pour une vérification de mot de passe).
- finish(name, result, text)  : le résultat rendu à l'appli (une vérification ne rend que oui ou non).
- NAMES                       : les actions connues.
- vérifications               : _folder_path, _device

Toutes les commandes de l'appli sur le serveur passent par ici (familles : disques, outils,
Nextcloud, système, réseau invités, sauvegardes, compte du serveur).
"""

import hmac
import re
import shlex

MOUNT_ROOTS = ("/srv/", "/mnt/", "/media/")
DEVICE = re.compile(r"/dev/(sd[a-z]{1,2}|nvme\d{1,2}n\d{1,2}|mmcblk\d{1,2}|hd[a-z])")
# Tous les caractères d'un nom de disque sont permis (accents, parenthèses, apostrophes…) :
# les commandes reçoivent le chemin comme argument séparé, jamais dans un texte de shell.
SAFE_PATH = re.compile(r"/[^\x00-\x1f\x7f]{1,250}")


class ActionError(ValueError):
    pass


def _folder_path(value):
    """Un chemin de disque ou de dossier partagé : sous /srv, /mnt ou /media, sans « .. »."""
    path = str(value or "")
    if not SAFE_PATH.fullmatch(path) or "/../" in path + "/" or "//" in path:
        raise ActionError(f"Chemin refusé : {path!r}")
    if path != "/srv/data" and not path.startswith(MOUNT_ROOTS):
        raise ActionError(f"Chemin hors des disques : {path!r}")
    return path


def _df_path(value):
    path = str(value or "")
    return path if path == "/" else _folder_path(path)


def _device(value):
    device = str(value or "")
    if not DEVICE.fullmatch(device):
        raise ActionError(f"Disque refusé : {device!r}")
    return device


# ---------------------------------------------------------------- la liste
def _server_name(args):
    return ["hostname"], 10


def _disk_space(args):
    paths = args.get("chemins") or []
    if not isinstance(paths, list) or not 0 < len(paths) <= 32:
        raise ActionError("Liste de disques refusée")
    return ["df", "-B1", "--output=target,size,used", *[_df_path(path) for path in paths]], 30


def _smartctl_installed(args):
    return ["sh", "-c", "command -v smartctl"], 10


def _disk_health(args):
    command = ["smartctl", "-j", "-H", "-A", "-n", "standby"]
    if args.get("pont_sat"):
        command += ["-d", "sat"]
    return command + [_device(args.get("appareil"))], 60


def _is_mounted(args):
    return ["mountpoint", "-q", _folder_path(args.get("chemin"))], 10


def _read_fstab(args):
    return ["cat", "/etc/fstab"], 10


def _eject(args):
    # Le chemin est passé comme argument du shell ($1), jamais collé dans le texte de la commande.
    return ["sh", "-c", 'sync && umount "$1"', "ejecter", _folder_path(args.get("chemin"))], 120


def _partitions(args):
    return ["lsblk", "-J", "-b", "-o", "NAME,PATH,FSTYPE,MOUNTPOINT,ROTA,TYPE,MODEL"], 30


def _trim_state(args):
    return [
        "systemctl",
        "show",
        "fstrim.timer",
        "-p",
        "UnitFileState",
        "-p",
        "ActiveState",
        "-p",
        "LastTriggerUSec",
    ], 10


def _enable_trim(args):
    return ["systemctl", "enable", "--now", "fstrim.timer"], 60


def _measure_fragmentation(args):
    return ["e4defrag", "-c", _folder_path(args.get("chemin"))], 3600


def _defragment(args):
    return ["e4defrag", _folder_path(args.get("chemin"))], 6 * 3600


# ---------------------------------------------------------------- antivirus (ClamAV), un outil de l'Établi
ANTIVIRUS_FOLDER = "/opt/stacks/antivirus"
# L'image officielle, telle quelle : freshclam tient les signatures à jour et clamd les garde en
# mémoire (environ 1 Go). Jabba lui fait analyser les nouveaux dépôts avec clamdscan.
# /srv est vu en lecture seule ; aucun port ouvert.
ANTIVIRUS_COMPOSE = """services:
  antivirus:
    image: clamav/clamav:stable
    container_name: antivirus
    # Priorité basse : une longue analyse de disque ne ralentit pas le reste du serveur.
    cpu_shares: 256
    volumes:
      - signatures:/var/lib/clamav
      - /srv:/srv:ro
    restart: unless-stopped
volumes:
  signatures:
"""
MAX_SCAN_FILES = 200


def _install_antivirus(args):
    script = (
        f"mkdir -p {ANTIVIRUS_FOLDER} && cat > {ANTIVIRUS_FOLDER}/compose.yaml <<'FIN'\n"
        + ANTIVIRUS_COMPOSE
        + f"FIN\ncd {ANTIVIRUS_FOLDER} && docker compose up -d"
    )
    return ["sh", "-c", script], 1800


def _remove_antivirus(args):
    return ["sh", "-c", f"cd {ANTIVIRUS_FOLDER} && docker compose down -v && rm -rf {ANTIVIRUS_FOLDER}"], 300


def _scan_files(args):
    paths = args.get("chemins") or []
    if not isinstance(paths, list) or not 0 < len(paths) <= MAX_SCAN_FILES:
        raise ActionError("Liste de fichiers refusée")
    checked = [_folder_path(path) for path in paths]
    if any(not path.startswith("/srv/") for path in checked):
        raise ActionError("L'antivirus ne voit que /srv")
    # --fdpass : le fichier est ouvert ici et transmis à clamd, qui n'a pas besoin d'avoir le droit de le lire.
    command = ["docker", "exec", "antivirus", "clamdscan", "--no-summary", "--infected", "--fdpass", "--"]
    return command + checked, 1800


def _scan_disk(args):
    """Analyse complète d'un disque, en tâche de fond (des heures pour un gros disque), en priorité basse.
    Le résultat (lignes « chemin: menace FOUND ») se lit ensuite dans le journal de la tâche."""
    path = _folder_path(args.get("chemin"))
    if not path.startswith("/srv/"):
        raise ActionError("L'antivirus ne voit que /srv")
    script = "exec docker exec antivirus clamdscan --no-summary --infected --fdpass -- " + shlex.quote(path) + "\n"
    return _background(_task_unit(args.get("unite")), script, "sh", ("Nice=19", "IOSchedulingClass=idle"))


# ---------------------------------------------------------------- outils (conteneurs Docker)
CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
STACK_FOLDER = re.compile(r"/opt/(stacks/)?[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
# Chaque geste sur un outil : un script fixe ; le dossier ($1) et le conteneur d'adresse ($2) sont
# passés comme arguments du shell, jamais collés dans le texte.
TOOL_GESTURES = {
    "redemarrer": 'cd "$1" && docker compose restart',
    "demarrer": 'cd "$1" && docker compose up -d && { [ -z "$2" ] || docker start "$2" || true; }',
    "arreter": 'cd "$1" && docker compose stop && { [ -z "$2" ] || docker stop "$2" || true; }',
    "mettre_a_jour": 'cd "$1" && docker compose pull && docker compose up -d && docker image prune -f',
}


def _container(value):
    name = str(value or "")
    if not CONTAINER.fullmatch(name):
        raise ActionError(f"Conteneur refusé : {name!r}")
    return name


def _list_containers(args):
    return ["docker", "ps", "-a", "--format", "{{json .}}"], 30


def _containers_usage(args):
    return ["docker", "stats", "--no-stream", "--format", "{{json .}}"], 30


def _container_image(args):
    return ["docker", "inspect", "--format", "{{.Config.Image}}|{{.Image}}", _container(args.get("nom"))], 10


def _image_details(args):
    image = str(args.get("image") or "")
    if not IMAGE_ID.fullmatch(image):
        raise ActionError(f"Image refusée : {image!r}")
    template = '{{json .RepoDigests}}|{{index .Config.Labels "org.opencontainers.image.version"}}'
    return ["docker", "image", "inspect", "--format", template, image], 10


def _container_running(args):
    return ["docker", "inspect", "-f", "{{.State.Running}}", _container(args.get("nom"))], 10


def _tool(args):
    gesture = args.get("geste")
    if gesture not in TOOL_GESTURES:
        raise ActionError(f"Geste refusé : {gesture!r}")
    folder = str(args.get("dossier") or "")
    if not STACK_FOLDER.fullmatch(folder) or ".." in folder:
        raise ActionError(f"Dossier d'outil refusé : {folder!r}")
    address = _container(args["adresse"]) if args.get("adresse") else ""
    return ["sh", "-c", TOOL_GESTURES[gesture], "outil", folder, address], 1800


# ---------------------------------------------------------------- Nextcloud (occ)
# Les sous-commandes d'administration dont l'appli se sert, et aucune autre.
OCC_COMMANDS = frozenset(
    {
        "status",
        "app:list",
        "app:install",
        "app:enable",
        "config:app:get",
        "config:app:set",
        "config:app:delete",
        "files:scan",
        "files_external:list",
        "files_external:create",
        "files_external:applicable",
        "files_external:option",
        "files_external:delete",
        "user:list",
        "user:info",
        "user:add",
        "user:delete",
        "user:enable",
        "user:disable",
        "user:resetpassword",
        "user:setting",
        "group:list",
        "group:info",
        "group:add",
        "group:delete",
        "group:adduser",
        "group:removeuser",
        "groupfolders:list",
        "groupfolders:create",
        "groupfolders:delete",
        "groupfolders:rename",
        "groupfolders:group",
        "groupfolders:quota",
    }
)
OCC_BASE = ["-u", "www-data", "nextcloud", "php", "occ", "--no-warnings"]
# Le mot de passe arrive sur l'entrée standard et devient la variable OC_PASS lue par occ :
# il n'apparaît jamais dans la liste des processus du serveur.
WITH_PASSWORD = 'export OC_PASS="$(cat)"; exec "$@"'
INPUT_ACTIONS = frozenset({"nextcloud", "verifier_mot_de_passe_serveur", "changer_mot_de_passe_serveur"})


def _nextcloud(args):
    command = args.get("commande")
    if command not in OCC_COMMANDS:
        raise ActionError(f"Commande Nextcloud refusée : {command!r}")
    arguments = args.get("arguments") or []
    if not isinstance(arguments, list) or len(arguments) > 40:
        raise ActionError("Arguments Nextcloud refusés")
    for argument in arguments:
        if not isinstance(argument, str) or len(argument) > 8192 or "\x00" in argument:
            raise ActionError(f"Argument Nextcloud refusé : {argument!r}"[:200])
    timeout = args.get("delai", 60)
    if not isinstance(timeout, int) or not 5 <= timeout <= 3600:
        raise ActionError(f"Délai refusé : {timeout!r}")
    if args.get("mot_de_passe"):
        return [
            "sh",
            "-c",
            WITH_PASSWORD,
            "sh",
            "docker",
            "exec",
            "-e",
            "OC_PASS",
            *OCC_BASE,
            command,
            *arguments,
        ], timeout
    return ["docker", "exec", *OCC_BASE, command, *arguments], timeout


def check_input(name, text):
    """Un texte pour l'entrée standard n'est accepté que par les actions prévues pour."""
    if text is None:
        return None
    if name not in INPUT_ACTIONS or not isinstance(text, str) or len(text) > 4096:
        raise ActionError(f"Entrée refusée pour {name!r}")
    return text


def host_input(name, args, text):
    """Le texte donné à la commande sur le serveur. Le mot de passe à vérifier, lui, ne quitte pas le service."""
    if name == "verifier_mot_de_passe_serveur":
        return None
    if name == "changer_mot_de_passe_serveur":
        if not text or ":" in text or "\n" in text:
            raise ActionError("Mot de passe refusé (ni « : » ni retour à la ligne).")
        return f"{args['utilisateur']}:{text}\n"
    return text


def finish(name, result, text):
    """Pour une vérification de mot de passe, l'empreinte de /etc/shadow reste dans le service :
    l'appli reçoit seulement 0 (bon), 1 (mauvais) ou 2 (compte illisible ou verrouillé)."""
    if name != "verifier_mot_de_passe_serveur":
        return result
    from host import system_crypt

    code, output, error_output = result
    if code != 0:
        return code, "", error_output[:200]
    fields = output.split(":")
    password_hash = fields[1] if len(fields) > 1 else ""
    if not password_hash or password_hash[0] in "!*":
        return 2, "", "Compte verrouillé ou illisible."
    computed = system_crypt.hash_with_setting(text or "", password_hash) or ""
    if hmac.compare_digest(computed, password_hash):
        return 0, "ok", ""
    return 1, "", "Mot de passe incorrect."


# ---------------------------------------------------------------- système
TASKS_DIR = "/var/lib/jabba/taches"
TASK_UNIT = re.compile(r"jabba-[a-z]{2,20}-\d{1,12}")
APP_FOLDER = re.compile(r"/[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}")
# Une tâche de fond va au bout même si l'appli redémarre. Son script est posé dans un fichier (pas
# en ligne de commande : systemd y remplacerait ${…}), passé ici comme argument ($3), jamais
# collé dans le texte. Le fichier s'efface dès que le script démarre.
BACKGROUND = (
    'umask 077 && mkdir -p "$1" && printf "%s" "$3" > "$1/$2.sh" && D="$1" && U="$2" && shift 3'
    ' && exec systemd-run --unit="$U" --collect "$@" "$D/$U.sh"'
)
POWER_SCRIPT = (
    'for f in /sys/class/power_supply/*; do [ -e "$f/type" ] || continue; '
    'echo "$(cat $f/type)|$(cat $f/online 2>/dev/null)|$(cat $f/capacity 2>/dev/null)|$(cat $f/status 2>/dev/null)'
    '|$(cat $f/power_now 2>/dev/null)|$(cat $f/current_now 2>/dev/null)|$(cat $f/voltage_now 2>/dev/null)"; done'
)
REBOOT = "[ -x /usr/local/sbin/redemarrer ] && exec /usr/local/sbin/redemarrer; exec systemctl reboot"


def _background(unit, script, interpreter="sh", properties=()):
    """Les propriétés (Nice=10…) et l'interpréteur suivent le script, en arguments de systemd-run."""
    options = [part for prop in properties for part in ("-p", prop)]
    return ["sh", "-c", BACKGROUND, "tache", TASKS_DIR, unit, 'rm -f "$0"\n' + script, *options, interpreter], 30


def _task_unit(value):
    unit = str(value or "")
    if not TASK_UNIT.fullmatch(unit):
        raise ActionError(f"Tâche refusée : {unit!r}")
    return unit


def _app_folder(value):
    folder = str(value or "")
    if not APP_FOLDER.fullmatch(folder) or ".." in folder:
        raise ActionError(f"Dossier de l'appli refusé : {folder!r}")
    return folder


def _os_release(args):
    return ["cat", "/etc/os-release"], 10


def _kernel(args):
    return ["uname", "-r"], 10


def _reboot_required(args):
    return ["test", "-f", "/var/run/reboot-required"], 10


def _updates_log(args):
    return ["tail", "-n", "200", "/var/log/unattended-upgrades/unattended-upgrades.log"], 10


def _tailscale_devices(args):
    return ["tailscale", "status", "--json"], 10


def _power(args):
    return ["sh", "-c", POWER_SCRIPT], 10


def _reboot(args):
    return ["systemd-run", "--on-active=3", "sh", "-c", REBOOT], 10


def _restart_policy_read(args):
    from services import restart_policy

    return ["cat", restart_policy.CHOICE_FILE], 10


def _restart_policy_timer(args):
    from services import restart_policy

    return ["systemctl", "is-enabled", "--quiet", f"{restart_policy.UNIT}.timer"], 10


def _restart_policy_apply(args):
    from services import restart_policy

    mode = args.get("mode")
    if mode not in restart_policy.MODES:
        raise ActionError(f"Mode refusé : {mode!r}")
    return ["sh", "-c", restart_policy.apply_script(mode)], 60


def _app_folder_action(args):
    template = '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
    return ["docker", "inspect", "-f", template, "jabba-app"], 10


def _app_update_ready(args):
    return ["sh", "-c", 'test -d "$1/.git" && command -v git', "pret", _app_folder(args.get("dossier"))], 10


def _app_update_install(args):
    """La clé qui vérifie la signature est lue ICI, dans les fichiers du service : l'appli ne la fournit pas."""
    from services import app_update

    try:
        script = app_update.install_script(
            _app_folder(args.get("dossier")), str(args.get("tag") or ""), app_update.publication_key()
        )
    except ValueError as error:
        raise ActionError(str(error))
    return _background(_task_unit(args.get("unite")), script)


def _task_state(args):
    return ["systemctl", "show", "-p", "ActiveState", "--value", _task_unit(args.get("unite"))], 10


def _task_log(args):
    return ["journalctl", "-u", _task_unit(args.get("unite")), "--no-pager", "-o", "cat", "-n", "300"], 10


# ---------------------------------------------------------------- disques : ajout, réglage, oubli
# Le service modifie lui-même /etc/fstab, ligne par ligne : l'appli ne lui envoie jamais le fichier
# entier (ce qui lui permettrait d'y écrire n'importe quoi). Les chemins et noms passent par des
# variables (ENVIRON pour awk, $1… pour le shell), jamais dans le texte des scripts.
AUTO_DISKS_ROOT = "/srv/disques"
# exFAT, FAT et NTFS n'ont pas de droits par fichier : ils sont donnés au montage. Le compte du
# serveur (1000) en est propriétaire, et le groupe www-data (33, Nextcloud) peut aussi écrire ;
# les autres (Jellyfin…) lisent seulement.
SHARED_RIGHTS = "uid=1000,gid=33,umask=002"
DISK_FORMATS = {  # format reconnu -> (type pour fstab, options)
    "exfat": ("exfat", SHARED_RIGHTS),
    "vfat": ("vfat", SHARED_RIGHTS + ",utf8"),
    "ntfs": ("ntfs3", SHARED_RIGHTS),
    "ext4": ("ext4", ""),
}
UUID = re.compile(r"[0-9A-Fa-f][0-9A-Fa-f-]{3,35}")
DISK_FOLDER = re.compile(r"[\w.-]{1,60}")
DISK_NAME = re.compile(r"[\w .-]{1,60}")
FSTAB = "F=/etc/fstab\n"
DISK_TREE_COLUMNS = "NAME,PATH,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINT,RM,TRAN,TYPE,MODEL,SERIAL,HOTPLUG,PARTTYPE"
DECLARE_DISK = (
    FSTAB
    + """set -e
if P="$1" awk '!/^[[:space:]]*#/ && $2==ENVIRON["P"] {f=1} END {exit !f}' "$F"; then
  echo "Ce dossier est déjà déclaré." >&2; exit 3
fi
mkdir -p "$1"
cp "$F" "$F.jabba-bak"
[ -z "$(tail -c1 "$F")" ] || echo >> "$F"
printf '%s\\n%s\\n' "$2" "$3" >> "$F"
systemctl daemon-reload
U=$(systemd-escape --path --suffix=automount "$1")
systemctl start "$U"
ls "$1" >/dev/null 2>&1 || true
"""
)
DISK_MODE = (
    FSTAB
    + """set -e
T=$(mktemp)
if ! P="$1" M="$2" R="$3" awk '!/^[[:space:]]*#/ && NF>=4 && $2==ENVIRON["P"] {
  shared = ($3 == "exfat" || $3 == "vfat" || $3 == "ntfs3")
  n = split($4, o, ","); s = ENVIRON["M"]
  for (i = 1; i <= n; i++) {
    if (o[i] == "ro" || o[i] == "rw") continue
    if (shared && o[i] ~ /^(uid|gid|umask|fmask|dmask)=/) continue
    s = s "," o[i]
  }
  if (shared) s = s "," ENVIRON["R"]
  $4 = s; f = 1 }
  {print} END {exit !f}' "$F" > "$T"; then
  rm -f "$T"; echo "Ce disque n'est pas déclaré sur le serveur." >&2; exit 4
fi
cp "$F" "$F.jabba-bak"
cat "$T" > "$F"
rm -f "$T"
systemctl daemon-reload
umount "$1" 2>/dev/null || true
U=$(systemd-escape --path --suffix=automount "$1")
systemctl restart "$U" || true
ls "$1" >/dev/null 2>&1 || true
"""
)
FORGET_DISK = (
    FSTAB
    + """U=$(systemd-escape --path --suffix=automount "$1")
systemctl stop "$U" 2>/dev/null
umount "$1" 2>/dev/null
set -e
T=$(mktemp)
P="$1" C="$2" awk '!/^[[:space:]]*#/ && NF>=2 && $2==ENVIRON["P"] {next}
  {l = $0; gsub(/^[ \\t]+|[ \\t]+$/, "", l); if (l == ENVIRON["C"]) next}
  {print}' "$F" > "$T"
cp "$F" "$F.jabba-bak"
cat "$T" > "$F"
rm -f "$T"
systemctl daemon-reload
rmdir "$1" 2>/dev/null || true
"""
)
REARM_DISK = """ls "$1" >/dev/null 2>&1
U=$(systemd-escape --path --suffix=automount "$1")
systemctl daemon-reload
systemctl reset-failed "${U%.automount}.mount" 2>/dev/null
systemctl restart "$U"
ls "$1" >/dev/null 2>&1
true
"""


def _uuid(value):
    uuid = str(value or "")
    if not UUID.fullmatch(uuid):
        raise ActionError(f"Identifiant de disque refusé : {uuid!r}")
    return uuid


def _disk_name(value):
    name = str(value or "")
    if not DISK_NAME.fullmatch(name):
        raise ActionError(f"Nom de disque refusé : {name!r}")
    return name


def _auto_disk_path(value):
    """Un disque ajouté par l'appli : /srv/disques/<dossier>, rien d'autre."""
    path = str(value or "")
    folder = path.removeprefix(AUTO_DISKS_ROOT + "/")
    if folder == path or not DISK_FOLDER.fullmatch(folder) or folder in (".", ".."):
        raise ActionError(f"Disque refusé : {path!r}")
    return path


def comment_line(name):
    """Le commentaire posé au-dessus de la ligne d'un disque ajouté par l'appli."""
    return f"# Ajouté par l'appli Jabba : {name}"


def fstab_line(uuid, disk_format, path):
    media_type, options = DISK_FORMATS[disk_format]
    all_options = ["ro", options, "nofail", "x-systemd.automount", "x-systemd.device-timeout=5s"]
    options_text = ",".join(option for option in all_options if option)
    if disk_format in ("exfat", "vfat", "ntfs"):
        uuid = uuid.upper()
    return f"UUID={uuid} {path} {media_type} {options_text} 0 0"


def _disk_tree(args):
    return ["lsblk", "-J", "-b", "-o", DISK_TREE_COLUMNS], 30


def _disk_present(args):
    uuid = _uuid(args.get("uuid"))
    return ["sh", "-c", 'blkid -U "$1" >/dev/null || blkid -U "$2" >/dev/null', "present", uuid, uuid.upper()], 10


def _wake_disk(args):
    return ["ls", _folder_path(args.get("chemin"))], 10


def _rearm_disk(args):
    return ["sh", "-c", REARM_DISK, "rearmer", _folder_path(args.get("chemin"))], 30


def _declare_disk(args):
    disk_format = args.get("format")
    if disk_format not in DISK_FORMATS:
        raise ActionError(f"Format refusé : {disk_format!r}")
    path = _auto_disk_path(f"{AUTO_DISKS_ROOT}/{args.get('dossier')}")
    line = fstab_line(_uuid(args.get("uuid")), disk_format, path)
    comment = comment_line(_disk_name(args.get("nom")))
    return ["sh", "-c", DECLARE_DISK, "declarer", path, comment, line], 30


def _disk_mode(args):
    path = _folder_path(args.get("chemin"))
    if path == "/srv/data":
        raise ActionError("Le disque de données reste en écriture.")
    mode = "rw" if args.get("ecriture") is True else "ro"
    # Les droits partagés sont remis à chaque réglage : un disque déclaré avant la 0.15.1 (gid=1000)
    # devient inscriptible par Nextcloud la prochaine fois qu'on le règle.
    return ["sh", "-c", DISK_MODE, "mode", path, mode, SHARED_RIGHTS], 30


def _forget_disk(args):
    path = _auto_disk_path(args.get("chemin"))
    return ["sh", "-c", FORGET_DISK, "oublier", path, comment_line(_disk_name(args.get("nom")))], 60


def _install_smartmontools(args):
    return ["apt-get", "install", "-y", "smartmontools"], 600


def _battery_shutdown(args):
    return ["systemd-run", "--unit=jabba-arret", "--collect", "--on-active=60", "systemctl", "poweroff"], 10


def _cancel_battery_shutdown(args):
    return ["systemctl", "stop", "jabba-arret.timer"], 10


# ---------------------------------------------------------------- réseau invités (pare-feu)
FIREWALL_CHAIN = "JABBA-WIFI"
INTERFACE = re.compile(r"[A-Za-z0-9_.-]{1,15}")
# Une règle par port fermé au Wi-Fi ; la chaîne n'est refaite que si elle a changé.
FIREWALL = """I="$1"; shift
status=0
for T in iptables ip6tables; do
  "$T" -S DOCKER-USER >/dev/null 2>&1 || continue
  want=$(for p in "$@"; do echo "-A JABBA-WIFI -i $I -p tcp -m conntrack --ctorigdstport $p --ctdir ORIGINAL -j DROP"; done)
  have=$("$T" -S JABBA-WIFI 2>/dev/null | grep '^-A ')
  if [ "$want" = "$have" ] && "$T" -C DOCKER-USER -j JABBA-WIFI 2>/dev/null; then continue; fi
  "$T" -N JABBA-WIFI 2>/dev/null
  "$T" -F JABBA-WIFI || { status=1; continue; }
  for p in "$@"; do
    "$T" -A JABBA-WIFI -i "$I" -p tcp -m conntrack --ctorigdstport "$p" --ctdir ORIGINAL -j DROP || status=1
  done
  "$T" -C DOCKER-USER -j JABBA-WIFI 2>/dev/null || "$T" -I DOCKER-USER -j JABBA-WIFI || status=1
done
exit $status
"""


def _network_route(args):
    return ["ip", "-o", "-4", "route", "get", "1.1.1.1"], 10


def _wifi_firewall(args):
    interface = str(args.get("interface") or "")
    if not INTERFACE.fullmatch(interface):
        raise ActionError(f"Carte réseau refusée : {interface!r}")
    ports = args.get("ports")
    if not isinstance(ports, list) or len(ports) > 64:
        raise ActionError("Ports refusés")
    for port in ports:
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ActionError(f"Port refusé : {port!r}")
    return ["sh", "-c", FIREWALL, "pare-feu", interface, *(str(port) for port in sorted(ports))], 60


# ---------------------------------------------------------------- sauvegardes (restic)
SNAPSHOT_ID = re.compile(r"[0-9a-f]{8,64}")
RESTORE_STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}h\d{2}")
RESTORE_ROOT = "/srv/data/dossiers/Restaurations"
RESTORE = 'restic "$@" && chown -R 1000:1000 "$0"'
BACKUP_PROPERTIES = ("Nice=10", "IOSchedulingClass=idle")


def _data_folder(value):
    """Le dossier des données de l'appli (la clé des sauvegardes y est)."""
    folder = _app_folder(value)
    if not folder.endswith("/donnees"):
        raise ActionError(f"Dossier des données refusé : {folder!r}")
    return folder


def _snapshot_path(value):
    path = str(value or "")
    if not path.startswith("/") or len(path) > 4096 or "\x00" in path or "\n" in path:
        raise ActionError(f"Chemin dans la copie refusé : {path!r}"[:200])
    return path


def _restic_env(args):
    from services import backups

    disk = _folder_path(args.get("disque"))
    return [
        "env",
        f"RESTIC_REPOSITORY={disk}/{backups.REPOSITORY_FOLDER}",
        f"RESTIC_PASSWORD_FILE={_data_folder(args.get('donnees'))}/sauvegardes.cle",
        "RESTIC_CACHE_DIR=/var/cache/restic-jabba",
    ]


def _restic_installed(args):
    return ["sh", "-c", "command -v restic"], 10


def _install_restic(args):
    return ["apt-get", "install", "-y", "restic"], 600


def _restic(args):
    """Lire les copies, en parcourir une, ou en remettre un morceau dans Restaurations/ (jamais par-dessus)."""
    gesture = args.get("geste")
    env = _restic_env(args)
    if gesture == "copies":
        return [*env, "restic", "--no-lock", "snapshots", "--tag", "jabba", "--json"], 120
    snapshot = str(args.get("copie") or "")
    if not SNAPSHOT_ID.fullmatch(snapshot):
        raise ActionError(f"Copie refusée : {snapshot!r}")
    path = _snapshot_path(args.get("chemin"))
    if gesture == "parcourir":
        return [*env, "restic", "--no-lock", "ls", "--json", snapshot, path], 120
    if gesture != "restaurer":
        raise ActionError(f"Geste refusé : {gesture!r}")
    stamp = str(args.get("horodatage") or "")
    if not RESTORE_STAMP.fullmatch(stamp) or path == "/":
        raise ActionError("Restauration refusée")
    parent, name = path.rsplit("/", 1)
    target = f"{RESTORE_ROOT}/{stamp} ({snapshot})"
    restic_args = ["--no-lock", "restore", f"{snapshot}:{parent or '/'}", "--target", target, "--include", "/" + name]
    return [*env, "sh", "-c", RESTORE, RESTORE_ROOT, *restic_args], 3600


def _start_backup(args):
    """Le script de la sauvegarde est écrit ICI, à partir des réglages : l'appli ne fournit pas de script."""
    from services import backups

    sources = args.get("sources")
    if not isinstance(sources, list) or not sources or not set(sources) <= set(backups.PATHS):
        raise ActionError(f"Sources refusées : {sources!r}")
    settings = {"chemin": _folder_path(args.get("disque")), "sources": {key: True for key in sources}}
    script = backups.backup_script(settings, _data_folder(args.get("donnees")))
    return _background(_task_unit(args.get("unite")), script, "bash", BACKUP_PROPERTIES)


# ---------------------------------------------------------------- compte du serveur
SERVER_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
# Seulement un compte de personne (numéro 1000 à 59999) : jamais root ni un compte de service.
HUMAN_ACCOUNT = (
    'u=$(id -u "$1" 2>/dev/null) && [ "$u" -ge 1000 ] && [ "$u" -lt 60000 ] || { echo "Compte refusé" >&2; exit 5; }\n'
)


def _server_user(value):
    user = str(value or "")
    if not SERVER_USER.fullmatch(user):
        raise ActionError(f"Compte refusé : {user!r}")
    return user


def _first_account(args):
    return ["getent", "passwd", "1000"], 10


def _check_server_password(args):
    return [
        "sh",
        "-c",
        HUMAN_ACCOUNT + 'exec getent shadow "$1"',
        "verifier",
        _server_user(args.get("utilisateur")),
    ], 10


def _change_server_password(args):
    return ["sh", "-c", HUMAN_ACCOUNT + "exec chpasswd", "changer", _server_user(args.get("utilisateur"))], 30


ACTIONS = {
    "nom_du_serveur": _server_name,
    "espace_disques": _disk_space,
    "smartctl_installe": _smartctl_installed,
    "sante_disque": _disk_health,
    "est_monte": _is_mounted,
    "lire_fstab": _read_fstab,
    "ejecter": _eject,
    "liste_partitions": _partitions,
    "etat_trim": _trim_state,
    "activer_trim": _enable_trim,
    "mesurer_fragmentation": _measure_fragmentation,
    "defragmenter": _defragment,
    "installer_antivirus": _install_antivirus,
    "retirer_antivirus": _remove_antivirus,
    "analyser_fichiers": _scan_files,
    "analyser_disque": _scan_disk,
    "liste_conteneurs": _list_containers,
    "conso_conteneurs": _containers_usage,
    "image_du_conteneur": _container_image,
    "details_image": _image_details,
    "conteneur_actif": _container_running,
    "outil": _tool,
    "nextcloud": _nextcloud,
    "version_systeme": _os_release,
    "noyau": _kernel,
    "redemarrage_requis": _reboot_required,
    "journal_mises_a_jour": _updates_log,
    "appareils_tailscale": _tailscale_devices,
    "alimentation": _power,
    "redemarrer_serveur": _reboot,
    "lire_regle_redemarrage": _restart_policy_read,
    "minuteur_redemarrage": _restart_policy_timer,
    "regle_redemarrage": _restart_policy_apply,
    "dossier_appli": _app_folder_action,
    "appli_prete": _app_update_ready,
    "installer_appli": _app_update_install,
    "etat_tache": _task_state,
    "journal_tache": _task_log,
    "arbre_disques": _disk_tree,
    "disque_present": _disk_present,
    "reveiller_disque": _wake_disk,
    "rearmer_disque": _rearm_disk,
    "declarer_disque": _declare_disk,
    "mode_disque": _disk_mode,
    "oublier_disque": _forget_disk,
    "installer_smartmontools": _install_smartmontools,
    "arret_batterie": _battery_shutdown,
    "annuler_arret_batterie": _cancel_battery_shutdown,
    "route_reseau": _network_route,
    "pare_feu_wifi": _wifi_firewall,
    "restic_installe": _restic_installed,
    "installer_restic": _install_restic,
    "restic": _restic,
    "lancer_sauvegarde": _start_backup,
    "compte_serveur": _first_account,
    "verifier_mot_de_passe_serveur": _check_server_password,
    "changer_mot_de_passe_serveur": _change_server_password,
}
NAMES = tuple(ACTIONS)


def build(name, args=None):
    builder = ACTIONS.get(name)
    if builder is None:
        raise ActionError(f"Action inconnue : {name!r}")
    if args is not None and not isinstance(args, dict):
        raise ActionError("Paramètres refusés")
    return builder(args or {})
