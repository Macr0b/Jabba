"""Antivirus : installé de base, nouveaux fichiers du disque de données, analyse des disques choisis."""

import os

import pytest

from host import actions
from services import antivirus, antivirus_disks

NIGHT = 1_000_000 - (1_000_000 % 86400) + 3 * 3600  # 3 h du matin (UTC)


@pytest.fixture
def serveur(tmp_path, monkeypatch):
    data = tmp_path / "srv" / "data"
    r2 = tmp_path / "srv" / "disques" / "R2-D2"
    data.mkdir(parents=True)
    r2.mkdir(parents=True)
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setattr(antivirus, "STATE_FILE", str(tmp_path / "av.json"))
    monkeypatch.setattr(antivirus, "QUARANTINE", str(tmp_path / "srv" / ".quarantaine"))
    monkeypatch.setattr(antivirus, "deposit_folders", lambda: [str(data / "invites")])
    monkeypatch.setattr(antivirus, "installed", lambda: True)
    monkeypatch.setattr(antivirus_disks, "DATA_DISK", str(data))
    monkeypatch.setattr(antivirus_disks, "available_disks", lambda: {str(data): "Disque de données", str(r2): "R2-D2"})
    monkeypatch.setattr(antivirus_disks.config, "DEMO", False)
    monkeypatch.setattr(antivirus.config, "DEMO", False)
    alerts, requests = [], []
    monkeypatch.setattr(antivirus.notifications, "send", lambda *a, **k: alerts.append(a))
    monkeypatch.setattr(antivirus.activity_log, "record", lambda *a, **k: None)
    serveur = {"active": "active", "log": "", "scan": ""}

    def action(nom, **args):
        requests.append((nom, args))
        if nom == "analyser_fichiers":
            return (1 if serveur["scan"] else 0), serveur["scan"], ""
        if nom == "etat_tache":
            return 0, serveur["active"] + "\n", ""
        if nom == "journal_tache":
            return 0, serveur["log"], ""
        return 0, "", ""

    monkeypatch.setattr(antivirus.commands, "run_action", action)
    monkeypatch.setattr(antivirus_disks.commands, "run_action", action)
    return {"data": data, "r2": r2, "alertes": alerts, "requests": requests, **{"state": serveur}}


def write(chemin, content=b"x", when=None):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(content)
    if when:
        os.utime(chemin, (when, when))


def test_installed_by_default_only_with_enough_memory(serveur, monkeypatch):
    started = []
    monkeypatch.setattr(antivirus, "installed", lambda: False)
    monkeypatch.setattr(antivirus, "install", lambda: started.append(1) or True)
    assert antivirus.install_by_default(total_memory=2 * 1024**3) == "memoire_insuffisante"
    assert antivirus.install_by_default(total_memory=16 * 1024**3) == "memoire_insuffisante"  # décidé une fois
    assert started == []
    os.remove(antivirus.STATE_FILE)
    assert antivirus.install_by_default(total_memory=int(3.9 * 1000**3)) == "installe"  # « 4 Go » annoncés
    assert started == [1]


def test_never_reinstalled_after_removal(serveur, monkeypatch):
    monkeypatch.setattr(antivirus, "installed", lambda: False)
    monkeypatch.setattr(antivirus, "install", lambda: pytest.fail("ne doit pas s'installer"))
    antivirus.write_state({"removed": True})
    assert antivirus.install_by_default(total_memory=16 * 1024**3) == "deja_decide"


def test_new_files_on_the_data_disk(serveur):
    data = serveur["data"]
    write(data / "vieux.pdf", when=1)
    debut = os.stat(data / "vieux.pdf").st_ctime + 1
    antivirus_disks.scan_new_files(now=debut)  # premier passage : on part de maintenant
    assert not [d for d in serveur["requests"] if d[0] == "analyser_fichiers"]
    import time

    time.sleep(1.1)
    write(data / "Photos" / "nouveau.jpg")
    write(data / "invites" / "depot.zip")  # suivi à part (chaque minute)
    write(data / ".cache" / "x.bin")  # dossier caché
    right_now = os.stat(data / "Photos" / "nouveau.jpg").st_ctime + 60
    serveur["state"]["scan"] = f"{data}/Photos/nouveau.jpg: Eicar-Test-Signature FOUND\n"
    etat = antivirus_disks.scan_new_files(now=right_now)
    scans = [d[1]["paths"] for d in serveur["requests"] if d[0] == "analyser_fichiers"]
    assert scans == [[f"{data}/Photos/nouveau.jpg"]]
    assert not (data / "Photos" / "nouveau.jpg").exists() and etat["threats"][0]["file"] == "nouveau.jpg"
    assert "arrivé sur le disque de données" in serveur["alertes"][0][2]
    # une copie qui garde sa vieille date d'origine est vue aussi (date de changement)
    write(data / "copie-du-mac.doc", when=1)
    arrival = os.stat(data / "copie-du-mac.doc").st_ctime
    antivirus.update_state(lambda etat: etat.update(new_since=arrival - 1))  # le passage d'avant
    serveur["state"]["scan"] = ""
    antivirus_disks.scan_new_files(now=arrival + 60)
    assert [d[1]["paths"] for d in serveur["requests"] if d[0] == "analyser_fichiers"][-1] == [
        f"{data}/copie-du-mac.doc"
    ]


def test_full_disk_scan(serveur):
    r2 = serveur["r2"]
    write(r2 / "jeux" / "crack.exe")
    assert antivirus_disks.start_scan(str(r2), now=NIGHT)
    assert not antivirus_disks.start_scan(str(serveur["data"]), now=NIGHT)  # une seule à la fois
    started = [d for d in serveur["requests"] if d[0] == "analyser_disque"]
    assert started == [("analyser_disque", {"path": str(r2), "unit": f"jabba-antivirus-{NIGHT}"})]
    antivirus_disks.watch(now=NIGHT + 60)  # toujours en cours
    assert antivirus_disks.disks(antivirus.read_state())[1]["scan"]["state"] == "en_cours"
    serveur["state"].update(active="inactive", log=f"{r2}/jeux/crack.exe: Win.Trojan.Agent FOUND\n")
    antivirus_disks.watch(now=NIGHT + 3600)
    r2_state = antivirus_disks.disks(antivirus.read_state())[1]
    assert r2_state["scan"]["state"] == "fini" and r2_state["scan"]["threats"] == 1
    assert not (r2 / "jeux" / "crack.exe").exists()
    assert f"trouvé en analysant {r2}" in serveur["alertes"][0][2]


def test_read_only_disk_alert_without_moving(serveur, monkeypatch):
    monkeypatch.setattr(antivirus, "_quarantine", lambda path: (_ for _ in ()).throw(OSError("lecture seule")))
    etat = {}
    antivirus.record_threat(etat, "/srv/disques/R2-D2/x.exe", "Eicar", "trouvé", 1)
    assert etat["threats"][0]["quarantined"] is None
    assert serveur["alertes"][0][1] == "Fichier infecté trouvé" and "lecture seule" in serveur["alertes"][0][2]


def test_weekly_night_scan_of_checked_disks(serveur):
    data, r2 = str(serveur["data"]), str(serveur["r2"])
    assert [d["weekly"] for d in antivirus_disks.disks(antivirus.read_state())] == [True, False]
    antivirus_disks.watch(now=NIGHT - 4 * 3600)  # 23 h : pas la nuit d'analyse
    assert not [d for d in serveur["requests"] if d[0] == "analyser_disque"]
    antivirus_disks.watch(now=NIGHT)
    assert [d[1]["path"] for d in serveur["requests"] if d[0] == "analyser_disque"] == [data]
    serveur["state"]["active"] = "inactive"
    antivirus_disks.set_weekly(r2, True)
    antivirus_disks.watch(now=NIGHT + 600)  # data fini ; R2-D2 jamais analysé : à son tour
    antivirus_disks.watch(now=NIGHT + 660)
    assert [d[1]["path"] for d in serveur["requests"] if d[0] == "analyser_disque"] == [data, r2]
    antivirus_disks.watch(now=NIGHT + 86400)  # le lendemain : rien, la semaine n'est pas passée
    assert len([d for d in serveur["requests"] if d[0] == "analyser_disque"]) == 2


def test_scan_disk_action():
    commande, _ = actions.build("analyser_disque", {"path": "/srv/disques/R2-D2", "unit": "jabba-antivirus-12"})
    assert "clamdscan --no-summary --infected --fdpass -- /srv/disques/R2-D2" in commande[6]
    assert commande[7:] == ["-p", "Nice=19", "-p", "IOSchedulingClass=idle", "sh"]
    commande, _ = actions.build("analyser_disque", {"path": "/srv/disques/a b'; reboot", "unit": "jabba-antivirus-1"})
    assert "'/srv/disques/a b'\"'\"'; reboot'" in commande[6]  # entre guillemets : jamais exécuté
    for args in ({"path": "/etc", "unit": "jabba-antivirus-1"}, {"path": "/srv/data", "unit": "x"}):
        with pytest.raises(actions.ActionError):
            actions.build("analyser_disque", args)
