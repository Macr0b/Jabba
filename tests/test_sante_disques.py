"""Santé des disques : verdict des rapports SMART, disque endormi, alertes quand un disque se dégrade."""

import json

import pytest
from service_factice import faux_service

from services import disk_health

SAIN_SATA = {
    "smart_status": {"passed": True},
    "ata_smart_attributes": {
        "table": [{"id": 5, "raw": {"value": 0}}, {"id": 197, "raw": {"value": 0}}, {"id": 9, "raw": {"value": 99}}]
    },
    "temperature": {"current": 34},
    "power_on_time": {"hours": 4800},
}


def sata(**compteurs):
    table = [{"id": int(k[1:]), "raw": {"value": v}} for k, v in compteurs.items()]
    return {"smart_status": {"passed": True}, "ata_smart_attributes": {"table": table}}


@pytest.mark.parametrize(
    "rapport, etat, raison",
    [
        (SAIN_SATA, "bon", None),
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
def test_verdict(rapport, etat, raison):
    trouve, raisons, details = disk_health.assess(rapport)
    assert trouve == etat
    if raison:
        assert any(raison in r for r in raisons), raisons
    else:
        assert raisons == []


def test_details():
    _, _, details = disk_health.assess(SAIN_SATA)
    assert details == {"temperature": 34, "heures": 4800, "usure": None, "compteurs": {"5": 0, "197": 0}}


class FauxServeur:
    """Répond à lsblk et smartctl comme un vrai serveur avec deux disques (un interne, un USB)."""

    def __init__(self):
        self.rapports = {"/dev/sda": SAIN_SATA, "/dev/sdb": SAIN_SATA}
        self.endormis = set()
        self.commandes = []

    def __call__(self, commande, timeout=30, input_text=None):
        self.commandes.append(commande)
        if commande[0] == "lsblk":
            disques = [
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
            return 0, json.dumps({"blockdevices": disques}), ""
        if commande[0] == "smartctl":
            chemin = commande[-1]
            if chemin in self.endormis:
                return 2, json.dumps({"smartctl": {"messages": [{"string": "Device is in STANDBY mode, exit(2)"}]}}), ""
            return 0, json.dumps(self.rapports[chemin]), ""
        return 1, "", ""


@pytest.fixture
def serveur(tmp_path, monkeypatch):
    faux = FauxServeur()
    monkeypatch.setattr(disk_health, "STATE_FILE", str(tmp_path / "sante.json"))
    monkeypatch.setattr(disk_health.config, "DEMO", False)  # on teste la vraie lecture, avec un faux serveur
    faux_service(monkeypatch, faux)
    alertes = []
    monkeypatch.setattr(disk_health.notifications, "send", lambda *a, **k: alertes.append(a))
    faux.alertes = alertes
    return faux


def test_lecture_sans_reveiller_et_alerte_une_fois(serveur):
    etat = disk_health.check_all(now=1000)
    assert set(etat["disques"]) == {"S1", "U2"}
    assert all("-n" in c and "standby" in c for c in serveur.commandes if c[0] == "smartctl")
    assert not any("/dev/zram0" in c or "/dev/loop0" in c for c in serveur.commandes)
    assert serveur.alertes == []

    serveur.rapports["/dev/sdb"] = sata(a5=4)
    disk_health.check_all(now=2000)
    assert (
        len(serveur.alertes) == 1 and "R2" in serveur.alertes[0][1] and "4 secteurs remplacés" in serveur.alertes[0][2]
    )
    disk_health.check_all(now=3000)
    assert len(serveur.alertes) == 1  # rien de neuf : pas de nouvelle alerte
    serveur.rapports["/dev/sdb"] = sata(a5=9)
    disk_health.check_all(now=4000)
    assert len(serveur.alertes) == 2  # le compteur augmente : on prévient de nouveau
    serveur.rapports["/dev/sdb"] = sata(a5=9, a197=1)
    disk_health.check_all(now=5000)
    assert len(serveur.alertes) == 3 and "critique" in serveur.alertes[-1][1]


def test_disque_endormi_garde_sa_derniere_lecture(serveur):
    disk_health.check_all(now=1000)
    serveur.endormis.add("/dev/sdb")
    etat = disk_health.check_all(now=2000)
    assert etat["disques"]["U2"]["endormi"] is True and etat["disques"]["U2"]["etat"] == "bon"
    assert etat["disques"]["U2"]["lu"] == 1000


def test_disque_endormi_prend_le_nouveau_nom(serveur):
    """Un nom enregistré avant la 0.15.1 (« modèle /dev/sdb ») est remplacé même si le disque dort."""
    disk_health.check_all(now=1000)
    etat = disk_health._read_state()
    etat["disques"]["U2"]["nom"] = "R2 /dev/sdb"
    disk_health._write_state(etat)
    serveur.endormis.add("/dev/sdb")
    assert disk_health.check_all(now=2000)["disques"]["U2"]["nom"] == "Disque externe"


def test_disque_jamais_lu(serveur):
    serveur.endormis.add("/dev/sdb")
    etat = disk_health.check_all(now=1000)
    assert etat["disques"]["U2"]["etat"] == "inconnu"
    resume = disk_health.summary()
    assert {d["libelle"] for d in resume["disques"]} == {"En bonne santé", "Pas lu"}


def test_usb_essaie_aussi_le_pont_sat(serveur):
    serveur.rapports["/dev/sdb"] = {}
    disk_health.check_all(now=1000)
    usb = [c for c in serveur.commandes if c[0] == "smartctl" and c[-1] == "/dev/sdb"]
    assert ["-d", "sat"] == usb[1][-3:-1]


def test_une_fois_par_jour(serveur, monkeypatch):
    monkeypatch.setattr(disk_health, "installed", lambda: True)
    monkeypatch.setattr(disk_health.time, "time", lambda: 100_000)
    disk_health.watch_disk_health()
    lus = len(serveur.commandes)
    disk_health.watch_disk_health()
    assert len(serveur.commandes) == lus
    monkeypatch.setattr(disk_health.time, "time", lambda: 100_000 + 86_401)
    disk_health.watch_disk_health()
    assert len(serveur.commandes) > lus


# ---------------------------------------------------------------- suppression massive
def test_alerte_suppression_massive(monkeypatch):
    from services import mass_deletion

    alertes = []
    monkeypatch.setattr(mass_deletion.notifications, "send", lambda *a, **k: alertes.append(a))
    monkeypatch.setitem(mass_deletion.STATE, "derniere_alerte", 0)
    maintenant = 1_000_000
    corbeille = [{"supprime": maintenant - 60} for _ in range(199)] + [
        {"supprime": maintenant - 7200} for _ in range(50)
    ]

    async def lister():
        return corbeille

    monkeypatch.setattr(mass_deletion.trash, "list_entries", lister)
    mass_deletion.watch_mass_deletion(now=maintenant)
    assert alertes == []  # 199 dans l'heure : sous le seuil
    corbeille.append({"supprime": maintenant - 10})
    mass_deletion.watch_mass_deletion(now=maintenant)
    assert len(alertes) == 1 and "200 éléments" in alertes[0][2]
    mass_deletion.watch_mass_deletion(now=maintenant + 600)
    assert len(alertes) == 1  # une seule alerte par vague
    mass_deletion.watch_mass_deletion(now=maintenant + 7 * 3600)
    assert len(alertes) == 1  # la vague est passée : plus rien dans l'heure
