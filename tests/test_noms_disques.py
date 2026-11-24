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


def test_l_ecran_garde_le_reglage_pendant_que_le_disque_se_rouvre(monkeypatch):
    """Juste après « Écriture autorisée », le disque est démonté puis rouvert : l'interrupteur ne disparaît pas."""
    import json

    from services import files_source

    arbre = {
        "blockdevices": [
            {
                "name": "sdc",
                "type": "disk",
                "tran": "usb",
                "size": 4 * 10**12,
                "model": "WDC WD40NMZW",
                "children": [
                    {
                        "name": "sdc1",
                        "fstype": "exfat",
                        "label": "R2-D2",
                        "uuid": "AB12-CD34",
                        "mountpoint": None,
                        "size": 4 * 10**12,
                    }
                ],
            }
        ]
    }
    from services import disk_inventory

    monkeypatch.setattr(disk_inventory.config, "DEMO", False)
    monkeypatch.setattr(files_source, "wake_disks", lambda: None)
    monkeypatch.setattr(files_source, "known_disks", lambda: {"R2-D2": "/srv/disques/R2-D2"})
    monkeypatch.setattr(files_source, "disk_uuid", lambda path: "ab12-cd34")
    monkeypatch.setattr(files_source, "is_writable", lambda path: True)
    monkeypatch.setattr(
        disk_inventory.auto_disks, "read", lambda: {"disques": [{"chemin": "/srv/disques/R2-D2"}], "oublies": []}
    )
    monkeypatch.setattr(disk_inventory.commands, "run_action", lambda nom, **a: (0, json.dumps(arbre), ""))
    disque = disk_inventory.list_disks()[0]
    assert disque["titre"] == "R2-D2" and disque["genre"] == "Disque externe USB"
    partie = disque["partitions"][0]
    assert partie["disque_jabba"] == {"nom": "R2-D2", "ecriture": True, "auto": True}
    assert partie["role"] == "Disque externe (en cours d'ouverture)"


def test_nextcloud_relit_le_disque_apres_un_changement_de_droits(monkeypatch):
    """Nextcloud garde les droits de chaque fichier : sans relecture, il refuse de supprimer."""
    import json

    from services import files_source

    appels = []

    def occ(args, **k):
        appels.append(args)
        if args[0] == "files_external:list":
            return (
                0,
                json.dumps(
                    [{"mount_id": 3, "mount_point": "/R2-D2", "configuration": {"datadir": "/mnt/disques/R2-D2"}}]
                ),
                "",
            )
        if args[0] == "user:list":
            return 0, json.dumps({"rob": "Rob", "lea": "Léa"}), ""
        return 0, "", ""

    monkeypatch.setattr(files_source.commands, "occ", occ)
    monkeypatch.setattr(files_source, "is_writable", lambda path: True)
    monkeypatch.setattr(files_source, "_mount", lambda path: True)
    files_source.rescan_in_nextcloud("/srv/disques/R2-D2")
    assert ["files_external:option", "3", "readonly", "false"] in appels
    assert ["files:scan", "--path=/rob/files/R2-D2"] in appels and ["files:scan", "--path=/lea/files/R2-D2"] in appels
    appels.clear()
    monkeypatch.setattr(files_source, "_mount", lambda path: False)  # disque pas ouvert : on ne relit pas
    files_source.rescan_in_nextcloud("/srv/disques/R2-D2")
    assert appels == []
