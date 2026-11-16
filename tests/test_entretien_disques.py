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


def test_seuls_les_disques_a_plateaux_en_ext4_montes():
    # jamais le SSD, jamais l'exFAT, jamais un disque démonté ou hors des disques de données
    assert disk_maintenance.spinning_ext4_mounts(LSBLK["blockdevices"]) == [
        {"nom": "R2-D2", "chemin": "/srv/disques/R2-D2"}
    ]


@pytest.mark.parametrize(
    "sortie, score, niveau",
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
def test_score(sortie, score, niveau):
    assert disk_maintenance.parse_score(sortie) == score
    assert disk_maintenance.level(score) == niveau


@pytest.fixture
def serveur(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_maintenance, "STATE_FILE", str(tmp_path / "frag.json"))
    monkeypatch.setattr(disk_maintenance.config, "DEMO", False)
    etat = {
        "score": 12,
        "trim": "UnitFileState=enabled\nActiveState=active\nLastTriggerUSec=Mon 2026-11-16 00:01:02 CET\n",
    }
    lances = []

    def action(nom, **args):
        lances.append((nom, args))
        actions.build(nom, args)  # tout passe par la liste fermée
        if nom == "liste_partitions":
            return 0, json.dumps(LSBLK), ""
        if nom == "mesurer_fragmentation":
            return 0, f" Fragmentation score                 {etat['score']}\n", ""
        if nom == "etat_trim":
            return 0, etat["trim"], ""
        return 0, "", ""

    monkeypatch.setattr(disk_maintenance.commands, "run_action", action)
    alertes = []
    monkeypatch.setattr(disk_maintenance.notifications, "send", lambda *a, **k: alertes.append(a))
    monkeypatch.setattr(disk_maintenance.activity_log, "record", lambda *a, **k: None)
    return etat, lances, alertes


def test_trim(serveur):
    etat, lances, _ = serveur
    assert disk_maintenance.trim_state() == {"ssd": True, "actif": True, "dernier": "Mon 2026-11-16 00:01:02 CET"}
    etat["trim"] = "UnitFileState=disabled\nActiveState=inactive\nLastTriggerUSec=n/a\n"
    assert disk_maintenance.trim_state() == {"ssd": True, "actif": False, "dernier": None}
    assert disk_maintenance.enable_trim() is True
    assert ("activer_trim", {}) in lances


def test_mesure_et_alerte_une_seule_fois(serveur):
    etat, lances, alertes = serveur
    resultat = disk_maintenance.measure_all(now=1000)
    assert resultat["disques"] == {"/srv/disques/R2-D2": {"nom": "R2-D2", "score": 12, "lu": 1000}}
    assert [n for n, a in lances if n == "mesurer_fragmentation"] == [
        "mesurer_fragmentation"
    ]  # le SSD n'est jamais mesuré
    assert alertes == []
    etat["score"] = 60
    disk_maintenance.measure_all(now=2000)
    disk_maintenance.measure_all(now=3000)
    assert len(alertes) == 1 and "R2-D2" in alertes[0][1]


def test_defragmenter_refuse_hors_liste(serveur):
    with pytest.raises(actions.ActionError):
        disk_maintenance.start_defragment("/")
    with pytest.raises(actions.ActionError):
        disk_maintenance.start_defragment("/srv/../etc")


def test_une_tache_a_la_fois(serveur, monkeypatch):
    monkeypatch.setitem(disk_maintenance.RUNNING, "tache", "mesure")
    assert disk_maintenance.start_measure() is False
    assert disk_maintenance.start_defragment("/srv/disques/R2-D2") is False


def test_une_mesure_par_mois(serveur, monkeypatch):
    _, lances, _ = serveur
    lance = []
    monkeypatch.setattr(disk_maintenance, "start_measure", lambda: lance.append(1))
    monkeypatch.setattr(disk_maintenance.time, "time", lambda: 10_000_000)
    disk_maintenance.watch_fragmentation()
    assert lance == [1]
    disk_maintenance.measure_all(now=10_000_000)
    disk_maintenance.watch_fragmentation()
    assert lance == [1]
