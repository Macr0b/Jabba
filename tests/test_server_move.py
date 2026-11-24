"""Déménager le serveur : la copie sur l'ancien (script, état, fin), et la reprise par le script d'installation."""

import json
import os
import subprocess

import pytest
from conftest import ROOT

from host import actions
from services import server_move, tool_addresses

INSTALLER = open(os.path.join(ROOT, "installer.sh")).read()


def fake_server(dossier, place=10**12, tar_fails=False):
    """De faux docker, tar, du, df… : le script tourne pour de vrai, sans toucher au système."""
    bin_dir = dossier / "bin"
    bin_dir.mkdir()
    log = dossier / "appels.txt"
    log.touch()
    tools_ = {
        "docker": f'echo "docker $*" >> {log}\n'
        '[ "$1" = ps ] && printf "/opt/stacks/nextcloud\\n/opt/stacks/jabba-app\\n/opt/stacks/nextcloud\\n"\ntrue',
        "tar": f'echo "tar $*" >> {log}\n'
        + ("exit 2\n" if tar_fails else "")
        + 'while [ $# -gt 0 ]; do [ "$1" = -cpf ] && echo archive > "$2"; shift; done',
        "du": 'printf "100\\t/opt/stacks\\n"',
        "df": f'printf "Avail\\n{place}\\n"',
        "findmnt": 'case "$*" in *FSTYPE*) echo ext4;; *) echo rw,relatime;; esac',
        "mountpoint": "true",
        "mount": f'echo "mount $*" >> {log}',
        "crontab": 'echo "*/5 * * * * cron.php"',
        "tailscale": "echo '{}'",
        "hostname": "echo serveur-test",
        "getent": "echo 'rob:x:1000:1000::/home/rob:/bin/bash'",
        "sync": "true",
    }
    for nom, body in tools_.items():
        (bin_dir / nom).write_text("#!/bin/sh\n" + body + "\n")
        (bin_dir / nom).chmod(0o755)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin"}, log


def launch(tmp_path, with_files, **k):
    disque = tmp_path / "disque"
    disque.mkdir()
    donnees = tmp_path / "donnees"
    donnees.mkdir()
    env, log = fake_server(tmp_path, **k)
    script = server_move.move_script(str(disque), with_files, str(donnees))
    subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    etat = json.loads((donnees / "demenagement-etat.json").read_text())
    return disque / ".demenagement-jabba", etat, log.read_text()


def test_full_copy(tmp_path):
    dossier, etat, calls = launch(tmp_path, with_files=True)
    assert etat["code"] == 0 and etat["step"] == "fini"
    assert {f.name for f in dossier.iterdir()} == {
        "outils.tar",
        "fichiers.tar",
        "crontab",
        "tailscale-serve.json",
        "manifeste.json",
        "outils-arretes",
    }
    manifest = json.loads((dossier / "manifeste.json").read_text())
    assert manifest["serveur"] == "serveur-test" and manifest["compte"] == "rob" and manifest["fichiers"] is True
    # Les outils s'arrêtent (une fois chacun), jamais Jabba lui-même ; les adresses ne sont pas copiées.
    assert calls.count("nextcloud stop") == 1 and (dossier / "outils-arretes").read_text() == "/opt/stacks/nextcloud\n"
    assert "--exclude=stacks/adresse-*" in calls and "--numeric-owner" in calls
    assert " start" not in calls  # réussi : les outils restent arrêtés


def test_on_the_data_disk_files_are_not_copied_again(tmp_path):
    dossier, etat, calls = launch(tmp_path, with_files=False)
    assert etat["code"] == 0 and not (dossier / "fichiers.tar").exists()
    assert json.loads((dossier / "manifeste.json").read_text())["fichiers"] is False


def test_not_enough_room_nothing_is_stopped(tmp_path):
    dossier, etat, calls = launch(tmp_path, with_files=True, place=50)
    assert etat["code"] == 12 and etat["needed"] >= 100 and etat["free"] == 50
    assert " stop" not in calls and not dossier.exists()


def test_failed_copy_tools_restart(tmp_path):
    dossier, etat, calls = launch(tmp_path, with_files=True, tar_fails=True)
    assert etat["code"] == 14 and "nextcloud start" in calls
    assert not dossier.exists() and not dossier.with_name(".demenagement-jabba.tmp").exists()


def test_move_actions():
    commande, _ = actions.build(
        "demenager",
        {"disk": "/srv/data", "files": False, "unit": "jabba-demenage-1", "data": "/opt/stacks/j/donnees"},
    )
    assert "jabba-demenage-1" in commande and "outils.tar" in commande[6]
    for bad in ({"disk": "/etc", "files": False}, {"disk": "/srv/data", "files": "oui"}):
        with pytest.raises(actions.ActionError):
            actions.build("demenager", {"unit": "jabba-demenage-1", "data": "/opt/stacks/j/donnees", **bad})
    end = actions.build("fin_demenagement", {})[0]
    script = end[6]
    assert script.index("tailscale logout") < script.index("systemctl poweroff") and "sleep 30" in script
    cancel_ = actions.build("annuler_demenagement", {"disk": "/srv/disques/R2"})[0]
    assert cancel_[-1] == "/srv/disques/R2" and "compose --project-directory" in cancel_[2]
    assert 'rm -rf "$copy" "$copy.tmp"' in cancel_[2]
    with pytest.raises(actions.ActionError):
        actions.build("annuler_demenagement", {"disk": "/"})


@pytest.fixture
def card(tmp_path, monkeypatch):
    monkeypatch.setattr(server_move, "SETTINGS_FILE", str(tmp_path / "demenagement.json"))
    monkeypatch.setattr(server_move, "STATE_FILE", str(tmp_path / "demenagement-etat.json"))
    monkeypatch.setattr(server_move.config, "DEMO", False)
    monkeypatch.setattr(server_move.files_source, "is_mounted", lambda chemin: True)
    monkeypatch.setattr(server_move.files_source, "known_disks", lambda: {"R2": "/srv/disques/R2"})
    monkeypatch.setattr(server_move.backups, "host_data_dir", lambda: "/opt/stacks/j/donnees")
    monkeypatch.setattr(server_move.backups, "is_running", lambda r: False)
    monkeypatch.setattr(server_move.activity_log, "record", lambda *a, **k: None)
    started_ones = []
    monkeypatch.setattr(
        server_move.commands, "run_action", lambda nom, **k: started_ones.append((nom, k)) or (0, "", "")
    )
    return tmp_path, started_ones


def test_disk_choice_and_states(card):
    dossier, started_ones = card
    etat = server_move.state()
    assert etat["state"] == "rien" and [d["files"] for d in etat["disks"]] == [False, True]
    with pytest.raises(server_move.ServiceError):
        server_move.start("/srv/disques/inconnu")
    server_move.start("/srv/disques/R2")
    assert started_ones[0][0] == "demenager" and started_ones[0][1]["files"] is True
    started = json.loads((dossier / "demenagement.json").read_text())["started"]
    (dossier / "demenagement-etat.json").write_text(
        json.dumps({"start": started, "code": 12, "needed": 3 * 1024**3, "free": 0})
    )
    assert server_move.state()["state"] == "rate" and "Il faut 4 Go" in server_move.state()["error"]
    (dossier / "demenagement-etat.json").write_text(json.dumps({"start": started, "end": started + 60, "code": 0}))
    assert server_move.state()["state"] == "pret"
    server_move.cancel()
    assert started_ones[-1] == ("annuler_demenagement", {"disk": "/srv/disques/R2"})
    assert server_move.state()["state"] == "rien"  # l'ancien résultat ne revient pas


def test_shutdown_frees_the_tailscale_names(card, monkeypatch):
    dossier, started_ones = card
    with pytest.raises(server_move.ServiceError):
        server_move.finish()  # pas de copie prête
    monkeypatch.setattr(server_move, "state", lambda: {"state": "pret"})
    monkeypatch.setattr(
        server_move.json_files,
        "load",
        lambda chemin, default=None: {"nextcloud": "https://nextcloud.tailnet-test.ts.net"},
    )

    def run_action(nom, **k):
        started_ones.append((nom, k))
        output = (
            json.dumps({"Self": {"DNSName": "serveur-test.tailnet-test.ts.net."}})
            if nom == "appareils_tailscale"
            else ""
        )
        return 0, output, ""

    monkeypatch.setattr(server_move.commands, "run_action", run_action)
    deleted = []
    devices = [
        {"id": "1", "name": "nextcloud.tailnet-test.ts.net"},
        {"id": "2", "name": "serveur-test.tailnet-test.ts.net"},
        {"id": "3", "name": "iphone.tailnet-test.ts.net"},
    ]

    def api(method, chemin, *a):
        if method == "GET":
            return {"devices": devices}
        deleted.append(chemin)

    monkeypatch.setattr(tool_addresses, "_api", api)
    assert server_move.finish() == {"ok": True, "freed_names": 2}
    assert started_ones[0][0] == "fin_demenagement"  # l'arrêt est programmé avant de couper Tailscale
    assert deleted == ["/device/1", "/device/2"]  # le serveur en dernier, jamais les autres appareils


def test_without_tailscale_key_shutdown_still_happens(card, monkeypatch):
    _, started_ones = card
    monkeypatch.setattr(server_move, "state", lambda: {"state": "pret"})
    monkeypatch.setattr(tool_addresses, "_token", lambda: None)
    assert server_move.finish()["freed_names"] == 0 and started_ones[-1][0] == "fin_demenagement"


def test_addresses_rebuilt_on_their_own(monkeypatch):
    done = []
    monkeypatch.setattr(tool_addresses, "create_missing", lambda installed, who: done.append(installed))
    monkeypatch.setattr(tool_addresses, "_token", lambda: None)
    tool_addresses.watch()
    assert done == []  # sans clé : rien
    monkeypatch.setattr(tool_addresses, "_token", lambda: "tskey-api-test")
    from services import tools

    monkeypatch.setattr(tools, "list_tools", lambda: [{"id": "nextcloud"}, {"id": "jellyfin"}])
    tool_addresses.watch()
    assert done == [{"nextcloud", "jellyfin"}]


def test_the_installer_resumes_the_move():
    block = INSTALLER.split("# Déménagement d'un ancien serveur Jabba")[1].split("# ----")[0]
    assert (
        "/srv/data/.demenagement-jabba/manifeste.json" in block and "mount -o ro" in block
    )  # disque USB : lu seulement
    assert 'read -rp "  Reprendre ses comptes' in block  # on demande toujours avant
    assert block.index("outils.tar") < block.index("fichiers.tar") and "--numeric-owner" in block
    assert "rm -f /opt/stacks/jabba-app/donnees/adresses-outils.json" in block  # Jabba refait les adresses
    assert "/var/lib/jabba/reprise-faite" in block  # relancé : pas repris deux fois
    restarted = INSTALLER.split("# Outils repris d'un ancien serveur")[1].split("# ----")[0]
    assert "docker compose up -d" in restarted and 'case "$d" in /opt/stacks/*)' in restarted
    assert r'"$CIBLE_R" =~ ^http://127\.0\.0\.1:[0-9]+$' in restarted  # seulement des renvois vers le serveur
    # Reprise avant Tailscale (étape 7) : la clé d'accès de l'ancien serveur est réutilisée si elle vaut encore.
    assert INSTALLER.index("# Déménagement d'un ancien serveur") < INSTALLER.index("CLE_API_FICHIER=")
