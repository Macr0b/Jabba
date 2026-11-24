"""Les actions sur les disques : place, santé SMART, éjection, TRIM et fragmentation ; puis l'ajout
des disques (fstab), leur mode (lecture ou écriture) et leur oubli.

Chaque action : un nom, des paramètres vérifiés, une commande construite ici (host/actions.py
les rassemble). Sommaire : ACTIONS, le nom de chaque action et sa fonction.
  comment_line  : le commentaire posé au-dessus de la ligne d'un disque ajouté par l'appli
  fstab_line : la ligne /etc/fstab d'un disque (lecture seule, sans bloquer le démarrage s'il manque)
  interne : _server_name, _disk_space, _smartctl_installed, _disk_health, _is_mounted, _read_fstab, _eject, _partitions, _trim_state, _enable_trim, _measure_fragmentation, _defragment, _uuid, _disk_name, _auto_disk_path, _disk_tree, _disk_present, _wake_disk, _rearm_disk, _declare_disk, _disk_mode, _forget_disk, _install_smartmontools, _battery_shutdown, _ups_shutdown, _cancel_battery_shutdown
"""

import re

from host.action_common import ActionError, device, df_path, folder_path, script


# ---------------------------------------------------------------- la liste
def _server_name(args):
    return ["hostname"], 10


def _disk_space(args):
    paths = args.get("chemins") or []
    if not isinstance(paths, list) or not 0 < len(paths) <= 32:
        raise ActionError("Liste de disques refusée")
    return ["df", "-B1", "--output=target,size,used", *[df_path(path) for path in paths]], 30


def _smartctl_installed(args):
    return ["sh", "-c", "command -v smartctl"], 10


def _disk_health(args):
    command = ["smartctl", "-j", "-H", "-A", "-n", "standby"]
    if args.get("pont_sat"):
        command += ["-d", "sat"]
    return command + [device(args.get("appareil"))], 60


def _is_mounted(args):
    return ["mountpoint", "-q", folder_path(args.get("chemin"))], 10


def _read_fstab(args):
    return ["cat", "/etc/fstab"], 10


def _eject(args):
    # Le chemin est passé comme argument du shell ($1), jamais collé dans le texte de la commande.
    return ["sh", "-c", 'sync && umount "$1"', "ejecter", folder_path(args.get("chemin"))], 120


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
    return ["e4defrag", "-c", folder_path(args.get("chemin"))], 3600


def _defragment(args):
    return ["e4defrag", folder_path(args.get("chemin"))], 6 * 3600


# ---------------------------------------------------------------- disques : ajout, réglage, oubli
# Le service modifie lui-même /etc/fstab, ligne par ligne : l'appli ne lui envoie jamais le fichier
# entier (ce qui lui permettrait d'y écrire n'importe quoi). Les chemins et noms passent par des
# variables (ENVIRON pour awk, $1… pour le shell), jamais dans le texte des scripts.
# Scripts : host/scripts/declare_disk.sh, disk_mode.sh, forget_disk.sh et rearm_disk.sh.
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
DISK_TREE_COLUMNS = "NAME,PATH,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINT,RM,TRAN,TYPE,MODEL,SERIAL,HOTPLUG,PARTTYPE"


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
    return ["ls", folder_path(args.get("chemin"))], 10


def _rearm_disk(args):
    return ["sh", "-c", script("rearm_disk"), "rearmer", folder_path(args.get("chemin"))], 30


def _declare_disk(args):
    disk_format = args.get("format")
    if disk_format not in DISK_FORMATS:
        raise ActionError(f"Format refusé : {disk_format!r}")
    path = _auto_disk_path(f"{AUTO_DISKS_ROOT}/{args.get('dossier')}")
    line = fstab_line(_uuid(args.get("uuid")), disk_format, path)
    comment = comment_line(_disk_name(args.get("nom")))
    return ["sh", "-c", script("declare_disk"), "declarer", path, comment, line], 30


def _disk_mode(args):
    path = folder_path(args.get("chemin"))
    if path == "/srv/data":
        raise ActionError("Le disque de données reste en écriture.")
    mode = "rw" if args.get("ecriture") is True else "ro"
    # Les droits partagés sont remis à chaque réglage : un disque déclaré avant la 0.15.1 (gid=1000)
    # devient inscriptible par Nextcloud la prochaine fois qu'on le règle.
    return ["sh", "-c", script("disk_mode"), "mode", path, mode, SHARED_RIGHTS], 30


def _forget_disk(args):
    path = _auto_disk_path(args.get("chemin"))
    return ["sh", "-c", script("forget_disk"), "oublier", path, comment_line(_disk_name(args.get("nom")))], 60


def _install_smartmontools(args):
    return ["apt-get", "install", "-y", "smartmontools"], 600


def _battery_shutdown(args):
    return ["systemd-run", "--unit=jabba-arret", "--collect", "--on-active=60", "systemctl", "poweroff"], 10


def _ups_shutdown(args):
    # « upsmon -c fsd » : arrêt du serveur, puis l'onduleur coupe sa prise et la rallume au retour du courant.
    return ["systemd-run", "--unit=jabba-arret", "--collect", "--on-active=60", "upsmon", "-c", "fsd"], 10


def _cancel_battery_shutdown(args):
    return ["systemctl", "stop", "jabba-arret.timer"], 10


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
    "arbre_disques": _disk_tree,
    "disque_present": _disk_present,
    "reveiller_disque": _wake_disk,
    "rearmer_disque": _rearm_disk,
    "declarer_disque": _declare_disk,
    "mode_disque": _disk_mode,
    "oublier_disque": _forget_disk,
    "installer_smartmontools": _install_smartmontools,
    "arret_batterie": _battery_shutdown,
    "arret_onduleur": _ups_shutdown,
    "annuler_arret_batterie": _cancel_battery_shutdown,
}
