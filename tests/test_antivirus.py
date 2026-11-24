"""Antivirus : liste fermée, choix des fichiers à analyser, mise à l'écart, alerte."""

import os

import pytest

from host import actions
from services import antivirus


def test_antivirus_actions():
    commande, _ = actions.build(
        "analyser_fichiers", {"paths": ["/srv/data/invites/a.pdf", "/srv/data/invites/b c.zip"]}
    )
    assert commande[:8] == ["docker", "exec", "antivirus", "clamdscan", "--no-summary", "--infected", "--fdpass", "--"]
    assert commande[8:] == ["/srv/data/invites/a.pdf", "/srv/data/invites/b c.zip"]
    commande = actions.build("installer_antivirus")[0]
    assert commande[3] == "/opt/stacks/antivirus" and 'printf "%s" "$1" > compose.yaml' in commande[2]
    compose = commande[4]
    assert "image: clamav/clamav:stable" in compose and "/srv:/srv:ro" in compose and "ports" not in compose
    for chemins in ([], ["/etc/passwd"], ["/mnt/x"], ["/srv/../etc"], ["/srv/x"] * 201, "/srv/x"):
        with pytest.raises(actions.ActionError):
            actions.build("analyser_fichiers", {"paths": chemins})


def test_report():
    output = "/srv/data/invites/eicar.com: Eicar-Test-Signature FOUND\n/srv/data/invites/x y.zip: Win.Trojan.Agent-1 FOUND\nautre ligne\n"
    assert antivirus.parse_report(output) == {
        "/srv/data/invites/eicar.com": "Eicar-Test-Signature",
        "/srv/data/invites/x y.zip": "Win.Trojan.Agent-1",
    }
    assert antivirus.parse_report("") == {}


@pytest.fixture
def drop(tmp_path, monkeypatch):
    dossier = tmp_path / "srv" / "invites"
    (dossier / "sous").mkdir(parents=True)
    (dossier / ".cache").mkdir()
    monkeypatch.setattr(antivirus, "STATE_FILE", str(tmp_path / "av.json"))
    monkeypatch.setattr(antivirus, "QUARANTINE", str(tmp_path / "srv" / ".quarantaine"))
    monkeypatch.setattr(antivirus, "deposit_folders", lambda: [str(dossier)])
    alerts, log, scans = [], [], []
    monkeypatch.setattr(antivirus.notifications, "send", lambda *a, **k: alerts.append(a))
    monkeypatch.setattr(antivirus.activity_log, "record", lambda *a, **k: log.append(a))
    answer = {"code": 0, "output": ""}

    def action(nom, **args):
        assert nom == "analyser_fichiers"
        scans.append(sorted(args["paths"]))
        return answer["code"], answer["output"], ""

    monkeypatch.setattr(antivirus.commands, "run_action", action)
    return dossier, answer, alerts, scans


def write(chemin, content, age=120):
    chemin.write_bytes(content)
    os.utime(chemin, (1_000_000 - age, 1_000_000 - age))


def test_scans_once_and_quarantines(drop):
    dossier, answer, alerts, scans = drop
    write(dossier / "propre.pdf", b"ok")
    write(dossier / "sous" / "virus.exe", b"X5O!P%@AP")
    write(dossier / ".cache" / "cache.bin", b"z")  # dossier caché : ignoré
    write(dossier / "en-cours.mov", b"..", age=5)  # encore en train d'arriver : attend
    answer.update(code=1, output=f"{dossier}/sous/virus.exe: Eicar-Test-Signature FOUND\n")
    etat = antivirus.scan_pending(now=1_000_000)
    assert scans == [[f"{dossier}/propre.pdf", f"{dossier}/sous/virus.exe"]]
    assert not (dossier / "sous" / "virus.exe").exists()
    threat = etat["threats"][0]
    assert threat["file"] == "virus.exe" and threat["threat"] == "Eicar-Test-Signature"
    assert os.path.exists(threat["quarantined"]) and "/.quarantaine/" in threat["quarantined"]
    assert len(alerts) == 1 and "virus.exe" in alerts[0][2]
    # second passage : rien de neuf, rien d'analysé
    answer.update(code=0, output="")
    antivirus.scan_pending(now=1_000_000)
    assert len(scans) == 1
    # un fichier modifié est réanalysé
    write(dossier / "propre.pdf", b"modifie", age=60)
    antivirus.scan_pending(now=1_000_000)
    assert scans[-1] == [f"{dossier}/propre.pdf"]


def test_antivirus_not_ready_retry(drop):
    dossier, answer, _, scans = drop
    write(dossier / "a.txt", b"a")
    answer.update(code=2, output="")
    antivirus.scan_pending(now=1_000_000)
    answer.update(code=0)
    antivirus.scan_pending(now=1_000_000)
    assert scans == [[f"{dossier}/a.txt"], [f"{dossier}/a.txt"]]


def test_big_files_and_links_ignored(drop, monkeypatch):
    dossier, _, _, scans = drop
    monkeypatch.setattr(antivirus, "MAX_ANALYSED_BYTES", 10)
    write(dossier / "film.mkv", b"x" * 20)
    os.symlink("/etc/passwd", dossier / "lien")
    antivirus.scan_pending(now=1_000_000)
    assert scans == []
