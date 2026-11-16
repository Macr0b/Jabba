"""La liste fermée des actions que l'appli peut demander au serveur.

Chaque action a un nom et des paramètres vérifiés ; la commande est construite ICI, jamais
reçue de l'extérieur. Le service privilégié (host/host_service.py) n'exécute que ce qui
figure dans cette liste : une faille dans l'interface web ne peut pas faire lancer
n'importe quelle commande sur le serveur (recommandation ANSSI : moindre privilège).

Sommaire
- ActionError                 : paramètre refusé.
- build(name, args)           : (commande, délai) pour une action connue, sinon ActionError.
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
}
NAMES = tuple(ACTIONS)


def build(name, args=None):
    builder = ACTIONS.get(name)
    if builder is None:
        raise ActionError(f"Action inconnue : {name!r}")
    if args is not None and not isinstance(args, dict):
        raise ActionError("Paramètres refusés")
    return builder(args or {})
