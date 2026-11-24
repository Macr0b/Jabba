"""Santé des disques : verdict des rapports SMART, disque endormi, alertes quand un disque se dégrade."""

import json

import pytest
from fake_host_service import fake_service

from services import disk_health

HEALTHY_SATA = {
    "smart_status": {"passed": True},
    "ata_smart_attributes": {
        "table": [{"id": 5, "raw": {"value": 0}}, {"id": 197, "raw": {"value": 0}}, {"id": 9, "raw": {"value": 99}}]
    },
    "temperature": {"current": 34},
    "power_on_time": {"hours": 4800},
}


def sata(**counters):
    table = [{"id": int(k[1:]), "raw": {"value": v}} for k, v in counters.items()]
    return {"smart_status": {"passed": True}, "ata_smart_attributes": {"table": table}}


@pytest.mark.parametrize(
    "report, etat, reason",
    [
        (HEALTHY_SATA, "bon", None),
        (sata(a5=3), "a_surveiller", "3 secteurs remplacés"),
        (sata(a5=80), "critique", "80 secteurs remplacés"),
        (sata(a197=1), "critique", "1 secteurs en attente de remplacement"),
        (sata(a198=2), "critique", "2 secteurs illisibles"),
        (sata(a187=1), "a_surveiller", "1 erreurs non corrigées"),
        ({"smart_status": {"passed": False}}, "critique", "panne prochaine"),
        ({"smart_status": {"passed": True}, "temperature": {"current": 61}}, "a_surveiller", "Chaud : 61 °C"),
        ({"nvme_smart_health_information_log": {"critical_warning": 1}}, "critique", "NVMe"),
        ({"nvme_smart_health_information_log": {"percentage_used": 93}}, "a_surveiller", "Usé à 93 %"),
        ({"nvme_smart_health_information_log": {"media_errors": 4, "percentage_used": 3}}, "a_surveiller", "4 erreurs"),
        ({"nvme_smart_health_information_log": {"critical_warning": 0, "percentage_used": 12}}, "bon", None),
        ({}, "bon", None),
    ],
)
def test_verdict(report, etat, reason):
    found, reasons, details = disk_health.assess(report)
    assert found == etat
    if reason:
        assert any(reason in r for r in reasons), reasons
    else:
        assert reasons == []


def test_details():
    _, _, details = disk_health.assess(HEALTHY_SATA)
    assert details == {"temperature": 34, "hours": 4800, "wear": None, "counters": {"5": 0, "197": 0}}


class FakeServer:
    """Répond à lsblk et smartctl comme un vrai serveur avec deux disques (un interne, un USB)."""

    def __init__(self):
        self.reports = {"/dev/sda": HEALTHY_SATA, "/dev/sdb": HEALTHY_SATA}
        self.asleep = set()
        self.commands_seen = []

    def __call__(self, commande, timeout=30, input_text=None):
        self.commands_seen.append(commande)
        if commande[0] == "lsblk":
            disks = [
                {
                    "name": "sda",
                    "path": "/dev/sda",
                    "model": "SSD interne",
                    "serial": "S1",
                    "tran": "sata",
                    "type": "disk",
                },
                {"name": "sdb", "path": "/dev/sdb", "model": "R2", "serial": "U2", "tran": "usb", "type": "disk"},
                {"name": "loop0", "path": "/dev/loop0", "type": "loop"},
                {"name": "zram0", "path": "/dev/zram0", "type": "disk"},
            ]
            return 0, json.dumps({"blockdevices": disks}), ""
        if commande[0] == "smartctl":
            chemin = commande[-1]
            if chemin in self.asleep:
                return 2, json.dumps({"smartctl": {"messages": [{"string": "Device is in STANDBY mode, exit(2)"}]}}), ""
            return 0, json.dumps(self.reports[chemin]), ""
        return 1, "", ""


@pytest.fixture
def serveur(tmp_path, monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(disk_health, "STATE_FILE", str(tmp_path / "sante.json"))
    monkeypatch.setattr(disk_health.config, "DEMO", False)  # on teste la vraie lecture, avec un faux serveur
    fake_service(monkeypatch, fake)
    alerts = []
    monkeypatch.setattr(disk_health.notifications, "send", lambda *a, **k: alerts.append(a))
    fake.alerts = alerts
    return fake


def test_read_without_waking_and_alert_once(serveur):
    etat = disk_health.check_all(now=1000)
    assert set(etat["disks"]) == {"S1", "U2"}
    assert all("-n" in c and "standby" in c for c in serveur.commands_seen if c[0] == "smartctl")
    assert not any("/dev/zram0" in c or "/dev/loop0" in c for c in serveur.commands_seen)
    assert serveur.alerts == []

    serveur.reports["/dev/sdb"] = sata(a5=4)
    disk_health.check_all(now=2000)
    assert len(serveur.alerts) == 1 and "R2" in serveur.alerts[0][1] and "4 secteurs remplacés" in serveur.alerts[0][2]
    disk_health.check_all(now=3000)
    assert len(serveur.alerts) == 1  # rien de neuf : pas de nouvelle alerte
    serveur.reports["/dev/sdb"] = sata(a5=9)
    disk_health.check_all(now=4000)
    assert len(serveur.alerts) == 2  # le compteur augmente : on prévient de nouveau
    serveur.reports["/dev/sdb"] = sata(a5=9, a197=1)
    disk_health.check_all(now=5000)
    assert len(serveur.alerts) == 3 and "critique" in serveur.alerts[-1][1]


def test_sleeping_disk_keeps_its_last_reading(serveur):
    disk_health.check_all(now=1000)
    serveur.asleep.add("/dev/sdb")
    etat = disk_health.check_all(now=2000)
    assert etat["disks"]["U2"]["asleep"] is True and etat["disks"]["U2"]["state"] == "bon"
    assert etat["disks"]["U2"]["read"] == 1000


def test_sleeping_disk_takes_the_new_name(serveur):
    """Un nom enregistré avant la 0.15.1 (« modèle /dev/sdb ») est remplacé même si le disque dort."""
    disk_health.check_all(now=1000)
    etat = disk_health._read_state()
    etat["disks"]["U2"]["name"] = "R2 /dev/sdb"
    disk_health._write_state(etat)
    serveur.asleep.add("/dev/sdb")
    assert disk_health.check_all(now=2000)["disks"]["U2"]["name"] == "Disque externe"


def test_disk_never_read(serveur):
    serveur.asleep.add("/dev/sdb")
    etat = disk_health.check_all(now=1000)
    assert etat["disks"]["U2"]["state"] == "inconnu"
    resume = disk_health.summary()
    assert {d["label_text"] for d in resume["disks"]} == {"En bonne santé", "Pas lu"}


def test_usb_also_tries_the_sat_bridge(serveur):
    serveur.reports["/dev/sdb"] = {}
    disk_health.check_all(now=1000)
    usb = [c for c in serveur.commands_seen if c[0] == "smartctl" and c[-1] == "/dev/sdb"]
    assert ["-d", "sat"] == usb[1][-3:-1]


def test_once_a_day(serveur, monkeypatch):
    monkeypatch.setattr(disk_health, "installed", lambda: True)
    monkeypatch.setattr(disk_health.time, "time", lambda: 100_000)
    disk_health.watch_disk_health()
    read_items = len(serveur.commands_seen)
    disk_health.watch_disk_health()
    assert len(serveur.commands_seen) == read_items
    monkeypatch.setattr(disk_health.time, "time", lambda: 100_000 + 86_401)
    disk_health.watch_disk_health()
    assert len(serveur.commands_seen) > read_items


# ---------------------------------------------------------------- suppression massive
def test_mass_deletion_alert(monkeypatch):
    from services import mass_deletion

    alerts = []
    monkeypatch.setattr(mass_deletion.notifications, "send", lambda *a, **k: alerts.append(a))
    monkeypatch.setitem(mass_deletion.STATE, "last_alert", 0)
    right_now = 1_000_000
    trash_ = [{"deleted": right_now - 60} for _ in range(199)] + [{"deleted": right_now - 7200} for _ in range(50)]

    async def list_items():
        return trash_

    monkeypatch.setattr(mass_deletion.trash, "list_entries", list_items)
    mass_deletion.watch_mass_deletion(now=right_now)
    assert alerts == []  # 199 dans l'heure : sous le seuil
    trash_.append({"deleted": right_now - 10})
    mass_deletion.watch_mass_deletion(now=right_now)
    assert len(alerts) == 1 and "200 éléments" in alerts[0][2]
    mass_deletion.watch_mass_deletion(now=right_now + 600)
    assert len(alerts) == 1  # une seule alerte par vague
    mass_deletion.watch_mass_deletion(now=right_now + 7 * 3600)
    assert len(alerts) == 1  # la vague est passée : plus rien dans l'heure
