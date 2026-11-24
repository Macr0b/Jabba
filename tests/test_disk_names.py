"""Noms des disques : ce qu'on reconnaît (R2-D2, Disque système…), pas le modèle."""

from services import disk_names


def disque(*parts, **info):
    return {"type": "disk", "model": "WDC WD40NMZW-11GX6S2", "children": list(parts), **info}


def test_system_disk_and_technical_partitions():
    ssd = disque(
        {"mountpoint": "/boot/efi", "fstype": "vfat", "label": None},
        {"mountpoint": "/", "fstype": "ext4"},
        {"mountpoint": "[SWAP]", "fstype": "swap"},
    )
    assert disk_names.title(ssd) == "Disque système" and disk_names.kind(ssd) == "Disque interne"
    assert [disk_names.is_technical(part) for part in ssd["children"]] == [True, False, True]


def test_data_disk():
    assert disk_names.title(disque({"mountpoint": "/srv/data", "label": "data"})) == "Disque de données"


def test_external_disk_carries_its_name():
    r2 = disque(
        {"label": "EFI", "fstype": "vfat"},
        {"label": "R2-D2", "fstype": "exfat", "mountpoint": "/srv/disques/R2-D2"},
        tran="usb",
    )
    assert disk_names.title(r2) == "R2-D2" and disk_names.kind(r2) == "Disque externe USB"
    two = disque({"label": "Photos"}, {"label": "Films"}, rm=True)
    assert disk_names.title(two) == "Photos · Films"


def test_without_name():
    assert disk_names.title(disque({"label": None}, tran="usb")) == "Disque externe"
    assert disk_names.title(disque()) == "Disque interne"


def test_the_screen_keeps_the_setting_while_the_disk_reopens(monkeypatch):
    """Juste après « Écriture autorisée », le disque est démonté puis rouvert : l'interrupteur ne disparaît pas."""
    import json

    from services import files_source

    tree = {
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
    monkeypatch.setattr(disk_inventory.commands, "run_action", lambda nom, **a: (0, json.dumps(tree), ""))
    disque = disk_inventory.list_disks()[0]
    assert disque["titre"] == "R2-D2" and disque["genre"] == "Disque externe USB"
    part_ = disque["partitions"][0]
    assert part_["disque_jabba"] == {"nom": "R2-D2", "ecriture": True, "auto": True}
    assert part_["role"] == "Disque externe (en cours d'ouverture)"


def test_nextcloud_rescans_the_disk_after_a_rights_change(monkeypatch):
    """Nextcloud garde les droits de chaque fichier : sans relecture, il refuse de supprimer."""
    import json

    from services import files_source

    calls = []

    def occ(args, **k):
        calls.append(args)
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
    assert ["files_external:option", "3", "readonly", "false"] in calls
    assert ["files:scan", "--path=/rob/files/R2-D2"] in calls and ["files:scan", "--path=/lea/files/R2-D2"] in calls
    calls.clear()
    monkeypatch.setattr(files_source, "_mount", lambda path: False)  # disque pas ouvert : on ne relit pas
    files_source.rescan_in_nextcloud("/srv/disques/R2-D2")
    assert calls == []
