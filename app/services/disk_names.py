"""Noms des disques tels qu'on les reconnaît, pour l'écran Disques et la carte Santé.

Un disque physique porte le nom de ce qu'il contient, pas celui de son modèle :
  « Disque système » (Debian, /), « Disque de données » (/srv/data), sinon le nom des
  partitions qu'on a données au disque (R2-D2), sinon « Disque externe » / « Disque interne ».
Le modèle reste affiché en petit (utile pour le racheter).

Sommaire :
  title(device)        : le nom à afficher pour un disque de lsblk (avec ses partitions)
  kind(device)         : « Disque externe USB » ou « Disque interne »
  is_technical(part)   : partition qu'on ne touche jamais (démarrage, swap), repliée à l'écran
  is_external : vrai pour un disque amovible (USB, branché à chaud)
"""

TECHNICAL_MOUNTS = ("/boot/efi", "/boot", "[SWAP]")
TECHNICAL_LABELS = {"EFI", "SYSTEM", "RECOVERY", "MSR", "BOOT"}


def is_external(device):
    return bool(device.get("rm")) or device.get("tran") == "usb" or bool(device.get("hotplug"))


def is_technical(partition):
    mountpoint = partition.get("mountpoint") or ""
    return (
        mountpoint in TECHNICAL_MOUNTS
        or mountpoint.startswith("/boot/")
        or partition.get("fstype") == "swap"
        or (partition.get("label") or "").upper() in TECHNICAL_LABELS
    )


def title(device):
    partitions = device.get("children") or []
    mountpoints = {partition.get("mountpoint") for partition in partitions}
    if "/" in mountpoints:
        return "Disque système"
    if "/srv/data" in mountpoints:
        return "Disque de données"
    labels = []
    for partition in partitions:
        label = (partition.get("label") or "").strip()
        if label and not is_technical(partition) and label not in labels:
            labels.append(label)
    if labels:
        return " · ".join(labels)
    return "Disque externe" if is_external(device) else "Disque interne"


def kind(device):
    return "Disque externe USB" if is_external(device) else "Disque interne"
