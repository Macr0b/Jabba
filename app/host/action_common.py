"""Ce que partagent toutes les familles d'actions (host/actions.py) : le refus d'un paramètre, la
vérification des chemins, des disques et des noms d'hôte, et le lancement d'une tâche de fond sur le serveur.

Sommaire :
  ActionError           : paramètre refusé
  folder_path(value)    : un chemin de disque ou de dossier partagé, sous /srv, /mnt ou /media, sans « .. »
  df_path(value)        : pareil, ou « / »
  device(value)         : un disque (/dev/sda, /dev/nvme0n1…)
  host_name(value)      : un nom de domaine vérifié, en minuscules
  background(unit, script, interpreter, properties) : une tâche qui va au bout même si l'appli redémarre
  task_unit(value)      : le nom d'une tâche de fond (jabba-…-123)
"""

import re

MOUNT_ROOTS = ("/srv/", "/mnt/", "/media/")
DEVICE = re.compile(r"/dev/(sd[a-z]{1,2}|nvme\d{1,2}n\d{1,2}|mmcblk\d{1,2}|hd[a-z])")
# Tous les caractères d'un nom de disque sont permis (accents, parenthèses, apostrophes…) :
# les commandes reçoivent le chemin comme argument séparé, jamais dans un texte de shell.
SAFE_PATH = re.compile(r"/[^\x00-\x1f\x7f]{1,250}")


class ActionError(ValueError):
    pass


def folder_path(value):
    """Un chemin de disque ou de dossier partagé : sous /srv, /mnt ou /media, sans « .. »."""
    path = str(value or "")
    if not SAFE_PATH.fullmatch(path) or "/../" in path + "/" or "//" in path:
        raise ActionError(f"Chemin refusé : {path!r}")
    if path != "/srv/data" and not path.startswith(MOUNT_ROOTS):
        raise ActionError(f"Chemin hors des disques : {path!r}")
    return path


def df_path(value):
    path = str(value or "")
    return path if path == "/" else folder_path(path)


def device(value):
    device = str(value or "")
    if not DEVICE.fullmatch(device):
        raise ActionError(f"Disque refusé : {device!r}")
    return device


HOST_NAME = re.compile(r"(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def host_name(value):
    name = str(value or "").strip().lower().rstrip(".")
    if not HOST_NAME.fullmatch(name):
        raise ActionError(f"Adresse refusée : {value!r}")
    return name


TASKS_DIR = "/var/lib/jabba/taches"
TASK_UNIT = re.compile(r"jabba-[a-z]{2,20}-\d{1,12}")
# Une tâche de fond va au bout même si l'appli redémarre. Son script est posé dans un fichier (pas
# en ligne de commande : systemd y remplacerait ${…}), passé ici comme argument ($3), jamais
# collé dans le texte. Le fichier s'efface dès que le script démarre.
BACKGROUND = (
    'umask 077 && mkdir -p "$1" && printf "%s" "$3" > "$1/$2.sh" && D="$1" && U="$2" && shift 3'
    ' && exec systemd-run --unit="$U" --collect "$@" "$D/$U.sh"'
)


def background(unit, script, interpreter="sh", properties=()):
    """Les propriétés (Nice=10…) et l'interpréteur suivent le script, en arguments de systemd-run."""
    options = [part for prop in properties for part in ("-p", prop)]
    return ["sh", "-c", BACKGROUND, "tache", TASKS_DIR, unit, 'rm -f "$0"\n' + script, *options, interpreter], 30


def task_unit(value):
    unit = str(value or "")
    if not TASK_UNIT.fullmatch(unit):
        raise ActionError(f"Tâche refusée : {unit!r}")
    return unit
