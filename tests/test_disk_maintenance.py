"""Entretien des disques : TRIM des SSD, fragmentation des seuls disques à plateaux en ext4."""

import json

import pytest

from host import actions
from services import disk_maintenance

LSBLK = {
    "blockdevices": [
        {
            "name": "sda",
            "path": "/dev/sda",
            "type": "disk",
            "rota": False,
            "model": "SSD interne",
            "children": [
                {"name": "sda1", "path": "/dev/sda1", "fstype": "ext4", "mountpoint": "/", "type": "part"},
                {"name": "sda2", "path": "/dev/sda2", "fstype": "ext4", "mountpoint": "/srv/data", "type": "part"},
            ],
        },
        {
            "name": "sdb",
            "path": "/dev/sdb",
            "type": "disk",
            "rota": True,
            "model": "R2",
            "children": [
                {
                    "name": "sdb1",
                    "path": "/dev/sdb1",
                    "fstype": "ext4",
                    "mountpoint": "/srv/disques/R2-D2",
                    "type": "part",
                },
            ],
        },
        {
            "name": "sdc",
            "path": "/dev/sdc",
            "type": "disk",
            "rota": True,
            "children": [
                {
                    "name": "sdc1",
                    "path": "/dev/sdc1",
                    "fstype": "exfat",
                    "mountpoint": "/srv/disques/Photos",
                    "type": "part",
                },
            ],
        },
        {
            "name": "sdd",
            "path": "/dev/sdd",
            "type": "disk",
            "rota": "1",
            "children": [
                {"name": "sdd1", "path": "/dev/sdd1", "fstype": "ext4", "mountpoint": None, "type": "part"},
                {"name": "sdd2", "path": "/dev/sdd2", "fstype": "ext4", "mountpoint": "/home", "type": "part"},
            ],
        },
    ]
}


def test_only_mounted_ext4_spinning_disks():
    # jamais le SSD, jamais l'exFAT, jamais un disque démonté ou hors des disques de données
    assert disk_maintenance.spinning_ext4_mounts(LSBLK["blockdevices"]) == [
        {"name": "R2-D2", "path": "/srv/disques/R2-D2"}
    ]


@pytest.mark.parametrize(
    "output, score, level_",
    [
        (
            " Fragmentation score                 12\n [0-30 no problem: 31-55 a little bit fragmented: 56- needs defrag]",
            12,
            "faible",
        ),
        ("Fragmentation score : 40", 40, "moyenne"),
        (" Fragmentation score                 56", 56, "forte"),
        ("rien", None, "inconnue"),
    ],
)
def test_score(output, score, level_):
    assert disk_maintenance.parse_score(output) == score
    assert disk_maintenance.level(score) == level_


@pytest.fixture
def serveur(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_maintenance, "STATE_FILE", str(tmp_path / "frag.json"))
    monkeypatch.setattr(disk_maintenance.config, "DEMO", False)
    etat = {
        "score": 12,
        "trim": "UnitFileState=enabled\nActiveState=active\nLastTriggerUSec=Mon 2026-11-16 00:01:02 CET\n",
    }
    runs = []

    def action(nom, **args):
        runs.append((nom, args))
        actions.build(nom, args)  # tout passe par la liste fermée
        if nom == "liste_partitions":
            return 0, json.dumps(LSBLK), ""
        if nom == "mesurer_fragmentation":
            return 0, f" Fragmentation score                 {etat['score']}\n", ""
        if nom == "etat_trim":
            return 0, etat["trim"], ""
        return 0, "", ""

    monkeypatch.setattr(disk_maintenance.commands, "run_action", action)
    alerts = []
    monkeypatch.setattr(disk_maintenance.notifications, "send", lambda *a, **k: alerts.append(a))
    monkeypatch.setattr(disk_maintenance.activity_log, "record", lambda *a, **k: None)
    return etat, runs, alerts


def test_trim(serveur):
    etat, runs, _ = serveur
    assert disk_maintenance.trim_state() == {"ssd": True, "active": True, "last": "Mon 2026-11-16 00:01:02 CET"}
    etat["trim"] = "UnitFileState=disabled\nActiveState=inactive\nLastTriggerUSec=n/a\n"
    assert disk_maintenance.trim_state() == {"ssd": True, "active": False, "last": None}
    assert disk_maintenance.enable_trim() is True
    assert ("activer_trim", {}) in runs


def test_measure_and_alert_once(serveur):
    etat, runs, alerts = serveur
    result = disk_maintenance.measure_all(now=1000)
    assert result["disks"] == {"/srv/disques/R2-D2": {"name": "R2-D2", "score": 12, "read": 1000}}
    assert [n for n, a in runs if n == "mesurer_fragmentation"] == [
        "mesurer_fragmentation"
    ]  # le SSD n'est jamais mesuré
    assert alerts == []
    etat["score"] = 60
    disk_maintenance.measure_all(now=2000)
    disk_maintenance.measure_all(now=3000)
    assert len(alerts) == 1 and "R2-D2" in alerts[0][1]


def test_defragment_refused_outside_the_list(serveur):
    with pytest.raises(actions.ActionError):
        disk_maintenance.start_defragment("/")
    with pytest.raises(actions.ActionError):
        disk_maintenance.start_defragment("/srv/../etc")


def test_one_task_at_a_time(serveur, monkeypatch):
    monkeypatch.setitem(disk_maintenance.RUNNING, "task", "mesure")
    assert disk_maintenance.start_measure() is False
    assert disk_maintenance.start_defragment("/srv/disques/R2-D2") is False


def test_one_measure_a_month(serveur, monkeypatch):
    _, runs, _ = serveur
    started = []
    monkeypatch.setattr(disk_maintenance, "start_measure", lambda: started.append(1))
    monkeypatch.setattr(disk_maintenance.time, "time", lambda: 10_000_000)
    disk_maintenance.watch_fragmentation()
    assert started == [1]
    disk_maintenance.measure_all(now=10_000_000)
    disk_maintenance.watch_fragmentation()
    assert started == [1]
