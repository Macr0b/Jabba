"""Antivirus : installé de base, nouveaux fichiers du disque de données, analyse des disques choisis."""

import os

import pytest

from host import actions
from services import antivirus, antivirus_disks

NUIT = 1_000_000 - (1_000_000 % 86400) + 3 * 3600  # 3 h du matin (UTC)


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
    alertes, demandes = [], []
    monkeypatch.setattr(antivirus.notifications, "send", lambda *a, **k: alertes.append(a))
    monkeypatch.setattr(antivirus.activity_log, "record", lambda *a, **k: None)
    serveur = {"actif": "active", "journal": "", "analyse": ""}

    def action(nom, **args):
        demandes.append((nom, args))
        if nom == "analyser_fichiers":
            return (1 if serveur["analyse"] else 0), serveur["analyse"], ""
        if nom == "etat_tache":
            return 0, serveur["actif"] + "\n", ""
        if nom == "journal_tache":
            return 0, serveur["journal"], ""
        return 0, "", ""

    monkeypatch.setattr(antivirus.commands, "run_action", action)
    monkeypatch.setattr(antivirus_disks.commands, "run_action", action)
    return {"data": data, "r2": r2, "alertes": alertes, "demandes": demandes, **{"etat": serveur}}


def ecrire(chemin, contenu=b"x", quand=None):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(contenu)
    if quand:
        os.utime(chemin, (quand, quand))


def test_installe_de_base_seulement_avec_assez_de_memoire(serveur, monkeypatch):
    lance = []
    monkeypatch.setattr(antivirus, "installed", lambda: False)
    monkeypatch.setattr(antivirus, "install", lambda: lance.append(1) or True)
    assert antivirus.install_by_default(total_memory=2 * 1024**3) == "memoire_insuffisante"
    assert antivirus.install_by_default(total_memory=16 * 1024**3) == "memoire_insuffisante"  # décidé une fois
    assert lance == []
    os.remove(antivirus.STATE_FILE)
    assert antivirus.install_by_default(total_memory=int(3.9 * 1000**3)) == "installe"  # « 4 Go » annoncés
    assert lance == [1]


def test_jamais_reinstalle_apres_un_retrait(serveur, monkeypatch):
    monkeypatch.setattr(antivirus, "installed", lambda: False)
    monkeypatch.setattr(antivirus, "install", lambda: pytest.fail("ne doit pas s'installer"))
    antivirus.write_state({"retire": True})
    assert antivirus.install_by_default(total_memory=16 * 1024**3) == "deja_decide"


def test_nouveaux_fichiers_du_disque_de_donnees(serveur):
    data = serveur["data"]
    ecrire(data / "vieux.pdf", quand=1)
    debut = os.stat(data / "vieux.pdf").st_ctime + 1
    antivirus_disks.scan_new_files(now=debut)  # premier passage : on part de maintenant
    assert not [d for d in serveur["demandes"] if d[0] == "analyser_fichiers"]
    import time

    time.sleep(1.1)
    ecrire(data / "Photos" / "nouveau.jpg")
    ecrire(data / "invites" / "depot.zip")  # suivi à part (chaque minute)
    ecrire(data / ".cache" / "x.bin")  # dossier caché
    maintenant = os.stat(data / "Photos" / "nouveau.jpg").st_ctime + 60
    serveur["etat"]["analyse"] = f"{data}/Photos/nouveau.jpg: Eicar-Test-Signature FOUND\n"
    etat = antivirus_disks.scan_new_files(now=maintenant)
    analyses = [d[1]["chemins"] for d in serveur["demandes"] if d[0] == "analyser_fichiers"]
    assert analyses == [[f"{data}/Photos/nouveau.jpg"]]
    assert not (data / "Photos" / "nouveau.jpg").exists() and etat["menaces"][0]["fichier"] == "nouveau.jpg"
    assert "arrivé sur le disque de données" in serveur["alertes"][0][2]
    # une copie qui garde sa vieille date d'origine est vue aussi (date de changement)
    ecrire(data / "copie-du-mac.doc", quand=1)
    arrivee = os.stat(data / "copie-du-mac.doc").st_ctime
    antivirus.update_state(lambda etat: etat.update(nouveaux_depuis=arrivee - 1))  # le passage d'avant
    serveur["etat"]["analyse"] = ""
    antivirus_disks.scan_new_files(now=arrivee + 60)
    assert [d[1]["chemins"] for d in serveur["demandes"] if d[0] == "analyser_fichiers"][-1] == [
        f"{data}/copie-du-mac.doc"
    ]


def test_analyse_complete_d_un_disque(serveur):
    r2 = serveur["r2"]
    ecrire(r2 / "jeux" / "crack.exe")
    assert antivirus_disks.start_scan(str(r2), now=NUIT)
    assert not antivirus_disks.start_scan(str(serveur["data"]), now=NUIT)  # une seule à la fois
    lance = [d for d in serveur["demandes"] if d[0] == "analyser_disque"]
    assert lance == [("analyser_disque", {"chemin": str(r2), "unite": f"jabba-antivirus-{NUIT}"})]
    antivirus_disks.watch(now=NUIT + 60)  # toujours en cours
    assert antivirus_disks.disks(antivirus.read_state())[1]["analyse"]["etat"] == "en_cours"
    serveur["etat"].update(actif="inactive", journal=f"{r2}/jeux/crack.exe: Win.Trojan.Agent FOUND\n")
    antivirus_disks.watch(now=NUIT + 3600)
    r2_etat = antivirus_disks.disks(antivirus.read_state())[1]
    assert r2_etat["analyse"]["etat"] == "fini" and r2_etat["analyse"]["menaces"] == 1
    assert not (r2 / "jeux" / "crack.exe").exists()
    assert f"trouvé en analysant {r2}" in serveur["alertes"][0][2]


def test_disque_en_lecture_seule_on_previent_sans_deplacer(serveur, monkeypatch):
    monkeypatch.setattr(antivirus, "_quarantine", lambda path: (_ for _ in ()).throw(OSError("lecture seule")))
    etat = {}
    antivirus.record_threat(etat, "/srv/disques/R2-D2/x.exe", "Eicar", "trouvé", 1)
    assert etat["menaces"][0]["ecart"] is None
    assert serveur["alertes"][0][1] == "Fichier infecté trouvé" and "lecture seule" in serveur["alertes"][0][2]


def test_analyse_de_la_semaine_la_nuit_disques_coches(serveur):
    data, r2 = str(serveur["data"]), str(serveur["r2"])
    assert [d["chaque_semaine"] for d in antivirus_disks.disks(antivirus.read_state())] == [True, False]
    antivirus_disks.watch(now=NUIT - 4 * 3600)  # 23 h : pas la nuit d'analyse
    assert not [d for d in serveur["demandes"] if d[0] == "analyser_disque"]
    antivirus_disks.watch(now=NUIT)
    assert [d[1]["chemin"] for d in serveur["demandes"] if d[0] == "analyser_disque"] == [data]
    serveur["etat"]["actif"] = "inactive"
    antivirus_disks.set_weekly(r2, True)
    antivirus_disks.watch(now=NUIT + 600)  # data fini ; R2-D2 jamais analysé : à son tour
    antivirus_disks.watch(now=NUIT + 660)
    assert [d[1]["chemin"] for d in serveur["demandes"] if d[0] == "analyser_disque"] == [data, r2]
    antivirus_disks.watch(now=NUIT + 86400)  # le lendemain : rien, la semaine n'est pas passée
    assert len([d for d in serveur["demandes"] if d[0] == "analyser_disque"]) == 2


def test_action_analyser_un_disque():
    commande, _ = actions.build("analyser_disque", {"chemin": "/srv/disques/R2-D2", "unite": "jabba-antivirus-12"})
    assert "clamdscan --no-summary --infected --fdpass -- /srv/disques/R2-D2" in commande[6]
    assert commande[7:] == ["-p", "Nice=19", "-p", "IOSchedulingClass=idle", "sh"]
    commande, _ = actions.build(
        "analyser_disque", {"chemin": "/srv/disques/a b'; reboot", "unite": "jabba-antivirus-1"}
    )
    assert "'/srv/disques/a b'\"'\"'; reboot'" in commande[6]  # entre guillemets : jamais exécuté
    for args in ({"chemin": "/etc", "unite": "jabba-antivirus-1"}, {"chemin": "/srv/data", "unite": "x"}):
        with pytest.raises(actions.ActionError):
            actions.build("analyser_disque", args)
