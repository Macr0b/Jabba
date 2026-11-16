"""Antivirus : liste fermée, choix des fichiers à analyser, mise à l'écart, alerte."""

import os

import pytest

from host import actions
from services import antivirus


def test_actions_de_l_antivirus():
    commande, _ = actions.build(
        "analyser_fichiers", {"chemins": ["/srv/data/invites/a.pdf", "/srv/data/invites/b c.zip"]}
    )
    assert commande[:8] == ["docker", "exec", "antivirus", "clamdscan", "--no-summary", "--infected", "--fdpass", "--"]
    assert commande[8:] == ["/srv/data/invites/a.pdf", "/srv/data/invites/b c.zip"]
    script = actions.build("installer_antivirus")[0][2]
    assert "image: clamav/clamav:stable" in script and "/srv:/srv:ro" in script and "ports" not in script
    for chemins in ([], ["/etc/passwd"], ["/mnt/x"], ["/srv/../etc"], ["/srv/x"] * 201, "/srv/x"):
        with pytest.raises(actions.ActionError):
            actions.build("analyser_fichiers", {"chemins": chemins})


def test_rapport():
    sortie = "/srv/data/invites/eicar.com: Eicar-Test-Signature FOUND\n/srv/data/invites/x y.zip: Win.Trojan.Agent-1 FOUND\nautre ligne\n"
    assert antivirus.parse_report(sortie) == {
        "/srv/data/invites/eicar.com": "Eicar-Test-Signature",
        "/srv/data/invites/x y.zip": "Win.Trojan.Agent-1",
    }
    assert antivirus.parse_report("") == {}


@pytest.fixture
def depot(tmp_path, monkeypatch):
    dossier = tmp_path / "srv" / "invites"
    (dossier / "sous").mkdir(parents=True)
    (dossier / ".cache").mkdir()
    monkeypatch.setattr(antivirus, "STATE_FILE", str(tmp_path / "av.json"))
    monkeypatch.setattr(antivirus, "QUARANTINE", str(tmp_path / "srv" / ".quarantaine"))
    monkeypatch.setattr(antivirus, "deposit_folders", lambda: [str(dossier)])
    alertes, journal, analyses = [], [], []
    monkeypatch.setattr(antivirus.notifications, "send", lambda *a, **k: alertes.append(a))
    monkeypatch.setattr(antivirus.activity_log, "record", lambda *a, **k: journal.append(a))
    reponse = {"code": 0, "sortie": ""}

    def action(nom, **args):
        assert nom == "analyser_fichiers"
        analyses.append(sorted(args["chemins"]))
        return reponse["code"], reponse["sortie"], ""

    monkeypatch.setattr(antivirus.commands, "run_action", action)
    return dossier, reponse, alertes, analyses


def ecrire(chemin, contenu, age=120):
    chemin.write_bytes(contenu)
    os.utime(chemin, (1_000_000 - age, 1_000_000 - age))


def test_analyse_une_seule_fois_et_met_a_l_ecart(depot):
    dossier, reponse, alertes, analyses = depot
    ecrire(dossier / "propre.pdf", b"ok")
    ecrire(dossier / "sous" / "virus.exe", b"X5O!P%@AP")
    ecrire(dossier / ".cache" / "cache.bin", b"z")  # dossier caché : ignoré
    ecrire(dossier / "en-cours.mov", b"..", age=5)  # encore en train d'arriver : attend
    reponse.update(code=1, sortie=f"{dossier}/sous/virus.exe: Eicar-Test-Signature FOUND\n")
    etat = antivirus.scan_pending(now=1_000_000)
    assert analyses == [[f"{dossier}/propre.pdf", f"{dossier}/sous/virus.exe"]]
    assert not (dossier / "sous" / "virus.exe").exists()
    menace = etat["menaces"][0]
    assert menace["fichier"] == "virus.exe" and menace["menace"] == "Eicar-Test-Signature"
    assert os.path.exists(menace["ecart"]) and "/.quarantaine/" in menace["ecart"]
    assert len(alertes) == 1 and "virus.exe" in alertes[0][2]
    # second passage : rien de neuf, rien d'analysé
    reponse.update(code=0, sortie="")
    antivirus.scan_pending(now=1_000_000)
    assert len(analyses) == 1
    # un fichier modifié est réanalysé
    ecrire(dossier / "propre.pdf", b"modifie", age=60)
    antivirus.scan_pending(now=1_000_000)
    assert analyses[-1] == [f"{dossier}/propre.pdf"]


def test_antivirus_pas_pret_on_reessaie(depot):
    dossier, reponse, _, analyses = depot
    ecrire(dossier / "a.txt", b"a")
    reponse.update(code=2, sortie="")
    antivirus.scan_pending(now=1_000_000)
    reponse.update(code=0)
    antivirus.scan_pending(now=1_000_000)
    assert analyses == [[f"{dossier}/a.txt"], [f"{dossier}/a.txt"]]


def test_gros_fichiers_et_liens_ignores(depot, monkeypatch):
    dossier, _, _, analyses = depot
    monkeypatch.setattr(antivirus, "MAX_ANALYSED_BYTES", 10)
    ecrire(dossier / "film.mkv", b"x" * 20)
    os.symlink("/etc/passwd", dossier / "lien")
    antivirus.scan_pending(now=1_000_000)
    assert analyses == []
