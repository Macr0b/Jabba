"""La liste fermée des actions que l'appli peut demander au serveur.

Chaque action a un nom et des paramètres vérifiés ; la commande est construite ICI, jamais
reçue de l'extérieur. Le service privilégié (host/host_service.py) n'exécute que ce qui
figure dans cette liste : une faille dans l'interface web ne peut pas faire lancer
n'importe quelle commande sur le serveur (recommandation ANSSI : moindre privilège).

Sommaire
- ActionError                 : paramètre refusé.
- build(name, args)           : (commande, délai) pour une action connue, sinon ActionError.
- check_input(name, text)     : seules certaines actions reçoivent un texte sur l'entrée standard (un mot de passe).
- NAMES                       : les actions connues.
- vérifications               : _folder_path, _device

La migration se fait par familles (voir README, « Organisation du code ») : les actions
non encore migrées passent toujours par host.commands.run_on_host.
"""

import re

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


def _physical_disks(args):
    return ["lsblk", "-J", "-d", "-b", "-o", "NAME,PATH,MODEL,SERIAL,TRAN,TYPE,SIZE"], 30


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
INPUT_ACTIONS = frozenset({"nextcloud"})


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


# ---------------------------------------------------------------- système
TASKS_DIR = "/var/lib/jabba/taches"
TASK_UNIT = re.compile(r"jabba-[a-z]{2,20}-\d{1,12}")
APP_FOLDER = re.compile(r"/[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}")
# Une tâche de fond va au bout même si l'appli redémarre. Son script est posé dans un fichier (pas
# en ligne de commande : systemd y remplacerait ${…}), passé ici comme argument ($3), jamais
# collé dans le texte. Le fichier s'efface dès que le script démarre.
BACKGROUND = 'umask 077 && mkdir -p "$1" && printf "%s" "$3" > "$1/$2.sh" && exec systemd-run --unit="$2" --collect sh "$1/$2.sh"'
POWER_SCRIPT = (
    'for f in /sys/class/power_supply/*; do [ -e "$f/type" ] || continue; '
    'echo "$(cat $f/type)|$(cat $f/online 2>/dev/null)|$(cat $f/capacity 2>/dev/null)|$(cat $f/status 2>/dev/null)'
    '|$(cat $f/power_now 2>/dev/null)|$(cat $f/current_now 2>/dev/null)|$(cat $f/voltage_now 2>/dev/null)"; done'
)
REBOOT = "[ -x /usr/local/sbin/redemarrer ] && exec /usr/local/sbin/redemarrer; exec systemctl reboot"


def _background(unit, script):
    return ["sh", "-c", BACKGROUND, "tache", TASKS_DIR, unit, 'rm -f "$0"\n' + script], 30


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


ACTIONS = {
    "nom_du_serveur": _server_name,
    "espace_disques": _disk_space,
    "liste_disques": _physical_disks,
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
}
NAMES = tuple(ACTIONS)


def build(name, args=None):
    builder = ACTIONS.get(name)
    if builder is None:
        raise ActionError(f"Action inconnue : {name!r}")
    if args is not None and not isinstance(args, dict):
        raise ActionError("Paramètres refusés")
    return builder(args or {})
