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
}
NAMES = tuple(ACTIONS)


def build(name, args=None):
    builder = ACTIONS.get(name)
    if builder is None:
        raise ActionError(f"Action inconnue : {name!r}")
    if args is not None and not isinstance(args, dict):
        raise ActionError("Paramètres refusés")
    return builder(args or {})
