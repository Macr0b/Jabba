"""Ajout automatique des disques USB : on branche, c'est prêt.

Un disque USB jamais vu est déclaré sur le serveur en lecture seule
(/srv/disques/<nom>, ouverture automatique), puis ajouté à Nextcloud s'il est
activé. Jellyfin le voit dans /disques ; les médiathèques restent à créer à la main.
Les disques « oubliés » ne sont plus jamais ajoutés tout seuls.

Sommaire :
  read
  write
  known_disks              : nom -> chemin pour les disques ajoutés automatiquement
  fstab_uuids
  clean_name
  candidates               : partitions USB lisibles, avec un identifiant, absentes de /etc/fstab
  add                      : déclare le disque sur le serveur (action « declarer_disque »). Renvoie l'entrée créée
  link_to_nextcloud        : ajoute le disque à Nextcloud (lecture seule). Sans effet si Nextcloud est arrêté
  forget                   : retire le disque du serveur et de Nextcloud. Il ne sera plus ajouté tout seul
  resume                   : un disque oublié redevient un disque à ajouter automatiquement
  is_present               : le disque est-il branché ? (sans réveiller l'ouverture automatique)
  scan                     : un passage : ajoute les nouveaux disques. Appelé toutes les 15 secondes
  scan_disks
  link_disks_to_nextcloud
"""

import json
import os
import re
import threading
import time

from host import actions, actions_disks, commands

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
DISKS_FILE = os.path.join(DATA_DIR, "disques.json")
MOUNT_ROOT = "/srv/disques"
_LOCK = threading.Lock()

# Formats lisibles sans rien installer (options de montage : host/actions.py).
FORMATS = actions_disks.DISK_FORMATS
# Partitions techniques à ignorer (démarrage, récupération Windows) et taille minimale.
TECHNICAL_TYPES = {"c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "de94bba4-06d1-4d40-a16a-bfd50179d6ac", "0xef", "0x27"}
MIN_SIZE = 300 * 1024 * 1024
UNREADABLE = {"apfs": "APFS (format Mac récent)", "hfsplus": "Mac OS étendu (HFS+)", "btrfs": "Btrfs", "xfs": "XFS"}


def read():
    data = json_files.load(DISKS_FILE)
    data.setdefault("disques", [])  # {nom, chemin, uuid, format, ajoute}
    data.setdefault("oublies", [])  # uuid
    data.setdefault("signales", [])  # uuid illisibles déjà signalés
    return data


def write(data):
    json_files.save(DISKS_FILE, data)


def known_disks():
    """nom -> chemin pour les disques ajoutés automatiquement."""
    return {disk["nom"]: disk["chemin"] for disk in read()["disques"]}


def fstab_uuids(fstab):
    result = set()
    for line in fstab.splitlines():
        fields = line.split()
        if fields and not line.lstrip().startswith("#") and fields[0].upper().startswith("UUID="):
            result.add(fields[0].split("=", 1)[1].strip('"').lower())
    return result


def clean_name(label, uuid, taken):
    base = (label or "").strip() or f"Disque {uuid[:4].upper()}"
    base = re.sub(r"[^\w .-]", "", base)[:40].strip() or f"Disque {uuid[:4].upper()}"
    name, index = base, 2
    while name.lower() in taken:
        name, index = f"{base} {index}", index + 1
    folder = re.sub(r"[^\w.-]", "-", name).strip("-") or uuid[:8]
    return name, folder


def candidates():
    """Partitions USB lisibles, avec un identifiant, absentes de /etc/fstab."""
    code, output, _ = commands.run_action("arbre_disques")
    if code != 0:
        return [], []
    _, fstab, _ = commands.run_action("lire_fstab")
    already = fstab_uuids(fstab)
    good, bad = [], []
    for data in json.loads(output).get("blockdevices", []):
        if data.get("type") != "disk":
            continue
        usb = (
            data.get("tran") == "usb"
            or str(data.get("rm")) in ("1", "True", "true")
            or str(data.get("hotplug")) in ("1", "True", "true")
        )
        if not usb:
            continue
        for partition in data.get("children") or [data]:
            filesystem, uuid = (partition.get("fstype") or "").lower(), (partition.get("uuid") or "").lower()
            if not uuid or uuid in already or partition.get("mountpoint"):
                continue
            if (partition.get("parttype") or "").lower() in TECHNICAL_TYPES or (
                partition.get("label") or ""
            ).upper() in ("EFI", "RECOVERY"):
                continue
            try:
                if int(partition.get("size") or 0) < MIN_SIZE:
                    continue
            except (TypeError, ValueError):
                pass
            if filesystem in FORMATS:
                good.append(
                    {
                        "uuid": uuid,
                        "format": filesystem,
                        "etiquette": partition.get("label"),
                        "chemin_dev": partition.get("path"),
                    }
                )
            elif filesystem in UNREADABLE:
                bad.append({"uuid": uuid, "format": filesystem, "etiquette": partition.get("label")})
    return good, bad


def add(share, taken):
    """Déclare le disque sur le serveur (action « declarer_disque »). Renvoie l'entrée créée."""
    name, folder = clean_name(share["etiquette"], share["uuid"], taken)
    path = f"{MOUNT_ROOT}/{folder}"
    try:
        code, _, error_output = commands.run_action(
            "declarer_disque", uuid=share["uuid"], format=share["format"], dossier=folder, nom=name
        )
    except actions.ActionError as error:
        raise RuntimeError(str(error))
    if code != 0:
        raise RuntimeError(error_output[:200])
    input_path = {
        "nom": name,
        "chemin": path,
        "dossier": folder,
        "uuid": share["uuid"],
        "format": share["format"],
        "ajoute": int(time.time()),
        "nextcloud": None,
    }
    with _LOCK:
        data = read()
        data["disques"].append(input_path)
        write(data)
    return input_path


def link_to_nextcloud(occ, input_path):
    """Ajoute le disque à Nextcloud (lecture seule). Sans effet si Nextcloud est arrêté.
    `occ(args)` : la commande d'administration de Nextcloud (host.commands.occ)."""
    datadir = f"datadir=/mnt/disques/{input_path['dossier']}"
    code, output, _ = occ(["files_external:create", input_path["nom"], "local", "null::null", "-c", datadir])
    match = re.search(r"(\d+)\s*$", output.strip()) if code == 0 else None
    if not match:
        return False
    # Réservé aux admins : sans ça, chaque compte Nextcloud verrait le disque.
    occ(["files_external:applicable", "--add-group=admin", match.group(1)])
    occ(["files_external:option", match.group(1), "readonly", "true"])
    with _LOCK:
        data = read()
        for disk in data["disques"]:
            if disk["uuid"] == input_path["uuid"]:
                disk["nextcloud"] = int(match.group(1))
        write(data)
    return True


def forget(name):
    """Retire le disque du serveur et de Nextcloud. Il ne sera plus ajouté tout seul."""
    with _LOCK:
        data = read()
        disk = next((disk for disk in data["disques"] if disk["nom"] == name), None)
    if not disk:
        raise KeyError(name)
    code, _, error_output = commands.run_action("oublier_disque", chemin=disk["chemin"], nom=name)
    if code != 0:
        raise RuntimeError(error_output[:200])
    if disk.get("nextcloud"):
        commands.occ(["files_external:delete", "-y", str(disk["nextcloud"])])
    with _LOCK:
        data = read()
        data["disques"] = [other for other in data["disques"] if other["nom"] != name]
        if disk["uuid"] not in data["oublies"]:
            data["oublies"].append(disk["uuid"])
        write(data)
    return disk


def resume(uuid):
    """Un disque oublié redevient un disque à ajouter automatiquement."""
    with _LOCK:
        data = read()
        data["oublies"] = [forgotten_uuid for forgotten_uuid in data["oublies"] if forgotten_uuid != uuid.lower()]
        write(data)


def is_present(uuid):
    """Le disque est-il branché ? (sans réveiller l'ouverture automatique)"""
    if not uuid:
        return True
    try:
        return commands.run_action("disque_present", uuid=uuid)[0] == 0
    except actions.ActionError:
        return False


def scan(nextcloud_running, notify, other_names):
    """Un passage : ajoute les nouveaux disques. Appelé toutes les 15 secondes."""
    good, bad = candidates()
    data = read()
    for match in bad:
        if match["uuid"] not in data["signales"]:
            data["signales"].append(match["uuid"])
            write(data)
            notify(
                "nouveau_disque",
                "Disque illisible",
                f"« {match['etiquette'] or 'Sans nom'} » est en {UNREADABLE[match['format']]}. Le serveur ne sait pas le lire : "
                "reformate-le en exFAT depuis le Mac (Utilitaire de disque) pour qu'il soit lisible partout.",
                "/#/disques",
            )
    for good_disk in good:
        if good_disk["uuid"] in data["oublies"]:
            continue
        taken = {names.lower() for names in other_names()} | {disk["nom"].lower() for disk in read()["disques"]}
        try:
            input_path = add(good_disk, taken)
        except RuntimeError as error:
            notify(
                "nouveau_disque",
                "Disque non ajouté",
                f"« {good_disk['etiquette'] or 'Sans nom'} » : {error}",
                "/#/disques",
            )
            continue
        linked = nextcloud_running() and link_to_nextcloud(commands.occ, input_path)
        notify(
            "nouveau_disque",
            f"Nouveau disque : {input_path['nom']}",
            "Prêt, en lecture seule"
            + (", visible dans Nextcloud" if linked else "")
            + f". Jellyfin le voit dans /disques/{input_path['dossier']}.",
            "/#/disques",
        )


# Importés ici, en fin de fichier : files_source importe lui-même ce module.
from services import disk_browser, files_source, json_files, notifications  # noqa: E402


def scan_disks():
    files_source.wake_disks()  # un disque rebranché est ouvert tout seul, sans attendre qu'on le consulte
    scan(
        lambda: files_source.files_source() == "nextcloud",
        lambda entry, item, element, forgotten_uuid: notifications.send(
            entry, item, element, forgotten_uuid, family="disque"
        ),
        lambda: list(files_source.configured_disks()) + list(disk_browser.roots()),
    )


def link_disks_to_nextcloud():
    if files_source.files_source() != "nextcloud":
        return
    for disk in read()["disques"]:
        if not disk.get("nextcloud"):
            link_to_nextcloud(commands.occ, disk)
