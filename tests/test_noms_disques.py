"""Noms des disques : ce qu'on reconnaît (R2-D2, Disque système…), pas le modèle."""

from services import disk_names


def disque(*parties, **infos):
    return {"type": "disk", "model": "WDC WD40NMZW-11GX6S2", "children": list(parties), **infos}


def test_disque_systeme_et_partitions_techniques():
    ssd = disque(
        {"mountpoint": "/boot/efi", "fstype": "vfat", "label": None},
        {"mountpoint": "/", "fstype": "ext4"},
        {"mountpoint": "[SWAP]", "fstype": "swap"},
    )
    assert disk_names.title(ssd) == "Disque système" and disk_names.kind(ssd) == "Disque interne"
    assert [disk_names.is_technical(part) for part in ssd["children"]] == [True, False, True]


def test_disque_de_donnees():
    assert disk_names.title(disque({"mountpoint": "/srv/data", "label": "data"})) == "Disque de données"


def test_disque_externe_porte_son_nom():
    r2 = disque(
        {"label": "EFI", "fstype": "vfat"},
        {"label": "R2-D2", "fstype": "exfat", "mountpoint": "/srv/disques/R2-D2"},
        tran="usb",
    )
    assert disk_names.title(r2) == "R2-D2" and disk_names.kind(r2) == "Disque externe USB"
    deux = disque({"label": "Photos"}, {"label": "Films"}, rm=True)
    assert disk_names.title(deux) == "Photos · Films"


def test_sans_nom():
    assert disk_names.title(disque({"label": None}, tran="usb")) == "Disque externe"
    assert disk_names.title(disque()) == "Disque interne"
