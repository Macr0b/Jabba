"""Ajout automatique des disques USB : on branche, c'est prêt.

Un disque USB jamais vu est déclaré sur le serveur en lecture seule
(/srv/disques/<nom>, ouverture automatique), puis ajouté à Nextcloud s'il est
activé. Jellyfin le voit dans /disques ; les médiathèques restent à créer à la main.
Les disques « oubliés » ne sont plus jamais ajoutés tout seuls.
"""

import json
import os
import re
import threading
import time

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
DISKS_FILE = os.path.join(DATA_DIR, "disques.json")
MOUNT_ROOT = "/srv/disques"
_LOCK = threading.Lock()

# Formats lisibles sans rien installer, et options de montage.
FORMATS = {
    "exfat": ("exfat", "uid=1000,gid=1000"),
    "vfat": ("vfat", "uid=1000,gid=1000,utf8"),
    "ntfs": ("ntfs3", "uid=1000,gid=1000"),
    "ext4": ("ext4", ""),
}
# Partitions techniques à ignorer (démarrage, récupération Windows) et taille minimale.
TECHNICAL_TYPES = {"c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "de94bba4-06d1-4d40-a16a-bfd50179d6ac", "0xef", "0x27"}
MIN_SIZE = 300 * 1024 * 1024
UNREADABLE = {"apfs": "APFS (format Mac récent)", "hfsplus": "Mac OS étendu (HFS+)", "btrfs": "Btrfs", "xfs": "XFS"}


def read():
    try:
        with open(DISKS_FILE) as file:
            data = json.load(file)
    except (OSError, ValueError):
        data = {}
    data.setdefault("disques", [])  # {nom, chemin, uuid, format, ajoute}
    data.setdefault("oublies", [])  # uuid
    data.setdefault("signales", [])  # uuid illisibles déjà signalés
    return data


def write(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DISKS_FILE + ".tmp", "w") as file:
        json.dump(data, file, ensure_ascii=False)
    os.replace(DISKS_FILE + ".tmp", DISKS_FILE)


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


def candidates(host_runner):
    """Partitions USB lisibles, avec un identifiant, absentes de /etc/fstab."""
    code, output, _ = host_runner(
        ["lsblk", "-J", "-b", "-o", "NAME,PATH,FSTYPE,LABEL,UUID,TRAN,RM,TYPE,MOUNTPOINT,SIZE,HOTPLUG,PARTTYPE"]
    )
    if code != 0:
        return [], []
    _, fstab, _ = host_runner(["cat", "/etc/fstab"])
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


def add(host_runner, host_input_runner, share, taken):
    """Déclare le disque sur le serveur. Renvoie l'entrée créée."""
    name, folder = clean_name(share["etiquette"], share["uuid"], taken)
    path = f"{MOUNT_ROOT}/{folder}"
    media_type, options = FORMATS[share["format"]]
    options_text = ",".join(
        mount_options
        for mount_options in ["ro", options, "nofail", "x-systemd.automount", "x-systemd.device-timeout=5s"]
        if mount_options
    )
    uuid = share["uuid"].upper() if share["format"] in ("exfat", "vfat", "ntfs") else share["uuid"]
    line = f"UUID={uuid} {path} {media_type} {options_text} 0 0"
    _, fstab, _ = host_runner(["cat", "/etc/fstab"])
    new_value = fstab.rstrip("\n") + f"\n# Ajouté par l'appli Jabba : {name}\n{line}\n"
    code, _, error_output = host_input_runner(
        ["sh", "-c", f"mkdir -p '{path}' && cp /etc/fstab /etc/fstab.jabba-bak && cat > /etc/fstab"], new_value
    )
    if code != 0:
        raise RuntimeError(error_output[:200])
    _, unit, _ = host_runner(["systemd-escape", "--path", "--suffix=automount", path])
    host_runner(
        ["sh", "-c", f"systemctl daemon-reload && systemctl start '{unit.strip()}' && ls '{path}' >/dev/null 2>&1"],
        timeout=30,
    )
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


def link_to_nextcloud(host_runner, input_path):
    """Ajoute le disque à Nextcloud (lecture seule). Sans effet si Nextcloud est arrêté."""
    occ = ["docker", "exec", "-u", "www-data", "nextcloud", "php", "occ"]
    code, output, _ = host_runner(
        occ
        + [
            "files_external:create",
            input_path["nom"],
            "local",
            "null::null",
            "-c",
            f"datadir=/mnt/disques/{input_path['dossier']}",
        ],
        timeout=60,
    )
    match = re.search(r"(\d+)\s*$", output.strip()) if code == 0 else None
    if not match:
        return False
    # Réservé aux admins : sans ça, chaque compte Nextcloud verrait le disque.
    host_runner(occ + ["files_external:applicable", "--add-group=admin", match.group(1)], timeout=60)
    host_runner(occ + ["files_external:option", match.group(1), "readonly", "true"], timeout=60)
    with _LOCK:
        data = read()
        for disk in data["disques"]:
            if disk["uuid"] == input_path["uuid"]:
                disk["nextcloud"] = int(match.group(1))
        write(data)
    return True


def forget(host_runner, host_input_runner, name):
    """Retire le disque du serveur et de Nextcloud. Il ne sera plus ajouté tout seul."""
    with _LOCK:
        data = read()
        disk = next((disk for disk in data["disques"] if disk["nom"] == name), None)
    if not disk:
        raise KeyError(name)
    path = disk["chemin"]
    _, unit, _ = host_runner(["systemd-escape", "--path", "--suffix=automount", path])
    host_runner(["sh", "-c", f"systemctl stop '{unit.strip()}'; umount '{path}' 2>/dev/null; true"], timeout=60)
    _, fstab, _ = host_runner(["cat", "/etc/fstab"])
    lines = []
    for line in fstab.splitlines():
        fields = line.split()
        if len(fields) >= 2 and not line.lstrip().startswith("#") and fields[1] == path:
            continue
        if line.strip() == f"# Ajouté par l'appli Jabba : {name}":
            continue
        lines.append(line)
    host_input_runner(
        ["sh", "-c", "cp /etc/fstab /etc/fstab.jabba-bak && cat > /etc/fstab && systemctl daemon-reload"],
        "\n".join(lines) + "\n",
    )
    host_runner(["rmdir", path])
    if disk.get("nextcloud"):
        host_runner(
            [
                "docker",
                "exec",
                "-u",
                "www-data",
                "nextcloud",
                "php",
                "occ",
                "files_external:delete",
                "-y",
                str(disk["nextcloud"]),
            ],
            timeout=60,
        )
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


def is_present(host_runner, uuid):
    """Le disque est-il branché ? (sans réveiller l'ouverture automatique)"""
    if not uuid:
        return True
    code, output, _ = host_runner(["blkid", "-U", uuid], timeout=10)
    if code == 0:
        return True
    code, _, _ = host_runner(["blkid", "-U", uuid.upper()], timeout=10)
    return code == 0


def scan(host_runner, host_input_runner, nextcloud_running, notify, other_names):
    """Un passage : ajoute les nouveaux disques. Appelé toutes les 15 secondes."""
    good, bad = candidates(host_runner)
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
            input_path = add(host_runner, host_input_runner, good_disk, taken)
        except RuntimeError as e:
            notify(
                "nouveau_disque", "Disque non ajouté", f"« {good_disk['etiquette'] or 'Sans nom'} » : {e}", "/#/disques"
            )
            continue
        linked = nextcloud_running() and link_to_nextcloud(host_runner, input_path)
        notify(
            "nouveau_disque",
            f"Nouveau disque : {input_path['nom']}",
            "Prêt, en lecture seule"
            + (", visible dans Nextcloud" if linked else "")
            + f". Jellyfin le voit dans /disques/{input_path['dossier']}.",
            "/#/disques",
        )


# ---- ajouté au découpage de main.py
import os
import threading
from services import disk_browser
from services import notifications
from host import commands
from services import files_source


def scan_disks():
    files_source.wake_disks()  # un disque rebranché est ouvert tout seul, sans attendre qu'on le consulte
    scan(
        commands.run_on_host,
        commands.run_on_host_with_input,
        lambda: files_source.files_source() == "nextcloud",
        lambda entry, item, element, forgotten_uuid: notifications.send(entry, item, element, forgotten_uuid, family="disque"),
        lambda: list(files_source.configured_disks()) + list(disk_browser.roots()),
    )


def link_disks_to_nextcloud():
    if files_source.files_source() != "nextcloud":
        return
    for disk in read()["disques"]:
        if not disk.get("nextcloud"):
            link_to_nextcloud(commands.run_on_host, disk)
