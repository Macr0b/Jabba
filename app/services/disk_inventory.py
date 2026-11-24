"""L'écran Disques : chaque disque du serveur et ses partitions, avec leur rôle (système, données,
disque externe de Jabba…), leur place et leurs réglages.

Sommaire :
  list_disks()  : la liste des disques (lsblk), prête pour l'écran
  interne : _context, _role, _partition, _device
"""

import json

import config
from host import commands
from services import auto_disks, disk_names, files_source, measurements
from services.service_error import ServiceError

ROLES = {"/": "Système", "/boot/efi": "Démarrage", "/srv/data": "Données"}


def _context(tree):
    """Ce qu'il faut savoir pour chaque partition : leur place, les disques de Jabba, les disques oubliés."""
    mounted = [
        partition["mountpoint"]
        for device in tree
        for partition in device.get("children") or []
        if partition.get("mountpoint")
    ]
    known = {path: name for name, path in files_source.known_disks().items()}
    # Un disque de Jabba garde ses réglages même pendant qu'il se rouvre (juste après « Écriture autorisée »).
    by_uuid = {(files_source.disk_uuid(path) or "").lower(): path for path in known}
    by_uuid.pop("", None)
    saved = auto_disks.read()
    return {
        "usage": measurements.disk_space([mount for mount in mounted if mount.startswith("/")]) if mounted else {},
        "known": known,
        "by_uuid": by_uuid,
        "auto": {disk["chemin"] for disk in saved["disques"]},
        "forgotten": set(saved["oublies"]),
    }


def _role(mountpoint, jabba_path, context):
    if mountpoint in context["known"]:
        return "Disque externe"
    if jabba_path:
        return "Disque externe (en cours d'ouverture)"
    return ROLES.get(mountpoint, "Non utilisé" if not mountpoint else mountpoint)


def _partition(partition, context):
    """Une partition à montrer, ou None (vide, mémoire d'échange, morceau de /boot)."""
    mountpoint = partition.get("mountpoint")
    if partition.get("fstype") in (None, "swap") and not mountpoint:
        return None
    if mountpoint and mountpoint.startswith("/boot/") and mountpoint != "/boot/efi":
        return None
    uuid = (partition.get("uuid") or "").lower()
    jabba_path = mountpoint if mountpoint in context["known"] else context["by_uuid"].get(uuid)
    jabba_disk = None
    if jabba_path:
        jabba_disk = {
            "nom": context["known"][jabba_path],
            "ecriture": files_source.is_writable(jabba_path),
            "auto": jabba_path in context["auto"],
        }
    return {
        "nom": partition["name"],
        "taille": partition["size"],
        "type": partition.get("fstype"),
        "etiquette": partition.get("label"),
        "montage": mountpoint,
        "role": _role(mountpoint, jabba_path, context),
        "usage": context["usage"].get(mountpoint),
        "disque_jabba": jabba_disk,
        "oublie": uuid if uuid in context["forgotten"] and not mountpoint else None,
        "illisible": auto_disks.UNREADABLE.get((partition.get("fstype") or "").lower()),
        "technique": disk_names.is_technical(partition),
    }


def _device(device, context):
    partitions = [_partition(partition, context) for partition in device.get("children") or []]
    return {
        "nom": device["name"],
        "titre": disk_names.title(device),
        "genre": disk_names.kind(device),
        "modele": (device.get("model") or "").strip(),
        "taille": device["size"],
        "externe": disk_names.is_external(device),
        "partitions": [partition for partition in partitions if partition],
    }


def list_disks():
    if config.DEMO:
        from services.demo_data import DISKS

        return DISKS
    files_source.wake_disks()
    code, output, error_output = commands.run_action("arbre_disques")
    if code != 0:
        raise ServiceError(500, error_output[:200])
    tree = json.loads(output)["blockdevices"]
    context = _context(tree)
    return [
        _device(device, context)
        for device in tree
        if device.get("type") == "disk" and not device["name"].startswith(("loop", "zram", "sr"))
    ]
