"""Nextcloud et Jellyfin installés depuis l'Établi : fichiers d'installation, puis réglage fait par Jabba
(ce que l'ancienne étape 8 du guide faisait à la main)."""

import json
import os
import subprocess

import httpx
import pytest

from host import actions, catalog
from services import tool_credentials, tool_setup


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_credentials, "CREDENTIALS_FILE", str(tmp_path / "identifiants.json"))
    monkeypatch.setattr(tool_credentials.config, "DEMO", False)
    monkeypatch.setattr(tool_credentials.config, "ADMIN_USER", "rob")
    monkeypatch.setattr(tool_setup, "_wait", lambda ready, **k: ready())
    return tmp_path


def test_install_writes_the_settings(tmp_path):
    """Essai à blanc du script d'installation : chaque marque est remplacée, rien n'est laissé au hasard."""
    fake = tmp_path / "bin"
    fake.mkdir()
    scripts = {
        "docker": 'echo "$@" >> "$(dirname "$0")/appels"',
        "tailscale": '[ "$1" = status ] && echo \'  "DNSName": "serveur.tail1234.ts.net.",\'; [ "$1" = ip ] && echo 100.64.0.9; true',
        "getent": '[ "$1" = passwd ] && echo "rob:x:1000:1000::/home/rob:/bin/bash"; [ "$1" = group ] && echo "render:x:993:"; true',
        "hostname": "echo serveur",
        "ip": 'echo "1.1.1.1 via 192.168.1.1 dev en0 src 192.168.1.50 uid 0"',
    }
    for nom, body in scripts.items():
        (fake / nom).write_text("#!/bin/sh\n" + body + "\n")
        (fake / nom).chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}
    read_items = {}
    for outil in ("nextcloud", "jellyfin"):
        commande, _ = actions.build("installer_outil_option", {"outil": outil})
        commande[3] = str(tmp_path / outil)
        commande[6] = ""  # la préparation (dossiers de /srv, crontab) n'est pas jouée ici
        output = subprocess.run(commande, input="mot-de-passe-de-jabba-1234", text=True, capture_output=True, env=env)
        assert output.returncode == 0, output.stderr
        read_items[outil] = (tmp_path / outil / ".env").read_text()
        assert "@" not in read_items[outil]  # plus aucune marque
    nextcloud = dict(line.split("=", 1) for line in read_items["nextcloud"].splitlines() if line)
    assert nextcloud["NC_ADMIN_USER"] == "rob" and nextcloud["NC_ADMIN_PASSWORD"] == "'mot-de-passe-de-jabba-1234'"
    assert nextcloud["DB_ROOT_PASSWORD"] != nextcloud["DB_PASSWORD"] and len(nextcloud["DB_PASSWORD"]) == 48
    assert (
        nextcloud["NC_DOMAINS"] == '"serveur serveur.tail1234.ts.net nextcloud.tail1234.ts.net 192.168.1.50 100.64.0.9"'
    )
    assert read_items["jellyfin"].strip() == "TZ=Europe/Paris\nPUID=1000\nPGID=1000\nRENDER_GID=993"


def test_catalog():
    for outil in ("nextcloud", "jellyfin"):
        entry = catalog.OPTIONAL_TOOLS[outil]
        assert entry["mot_de_passe"] and entry["identifiant"] == catalog.SERVER_USER_MARK
        assert entry["port_https"] is None  # leur adresse à part, comme avant (nextcloud.…, jellyfin.…)
        assert "mkdir -p /srv/data/media/films" in entry["preparer"]
    assert "cron.php" in catalog.OPTIONAL_TOOLS["nextcloud"]["preparer"]
    assert catalog.NEXTCLOUD_CRON.count("crontab") == 2  # relue puis réécrite : une seule ligne, jamais en double


def test_login_named_after_the_server_account(vault):
    tool_credentials.password_for("nextcloud")
    assert tool_credentials.read("nextcloud")["identifiant"] == "rob"


class FakeJellyfin:
    def __init__(self, finished=False):
        self.finished = finished
        self.calls = []
        self.libraries = []

    def get(self, url, headers=None, timeout=None):
        chemin = url.removeprefix(tool_setup.JELLYFIN)
        self.calls.append(("GET", chemin))
        if chemin == "/System/Info/Public":
            return httpx.Response(200, json={"StartupWizardCompleted": self.finished})
        if chemin == "/Library/VirtualFolders":
            return httpx.Response(200, json=[{"Name": nom} for nom in self.libraries])
        return httpx.Response(200, json={})

    def post(self, url, json=None, params=None, headers=None, timeout=None):
        chemin = url.removeprefix(tool_setup.JELLYFIN)
        self.calls.append(("POST", chemin, json, params))
        request = httpx.Request("POST", url)
        if chemin == "/Users/AuthenticateByName":
            return httpx.Response(200, json={"AccessToken": "jeton"}, request=request)
        if chemin == "/Library/VirtualFolders":
            self.libraries.append(params["name"])
        return httpx.Response(204, request=request)


def test_jellyfin_wizard_and_libraries(vault, monkeypatch):
    fake = FakeJellyfin()
    monkeypatch.setattr(tool_setup.httpx, "get", fake.get)
    monkeypatch.setattr(tool_setup.httpx, "post", fake.post)
    monkeypatch.setattr(tool_setup.activity_log, "record", lambda *a, **k: None)
    word = tool_credentials.password_for("jellyfin")
    assert tool_setup.after_install("jellyfin").startswith("Jellyfin réglé")
    sent = [a[1:3] for a in fake.calls if a[0] == "POST"]
    assert ("/Startup/User", {"Name": "rob", "Password": word}) in sent
    assert ("/Startup/RemoteAccess", {"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False}) in sent
    assert fake.libraries == ["Films", "Séries"]
    # Relancé : l'assistant est fait, les médiathèques existent déjà ; rien n'est refait en double.
    fake.finished = True
    tool_setup.after_install("jellyfin")
    assert fake.libraries == ["Films", "Séries"]


def test_jellyfin_set_up_by_someone_else(vault, monkeypatch):
    fake = FakeJellyfin(finished=True)
    monkeypatch.setattr(tool_setup.httpx, "get", fake.get)
    monkeypatch.setattr(
        tool_setup.httpx, "post", lambda url, **k: httpx.Response(401, request=httpx.Request("POST", url))
    )
    tool_credentials.password_for("jellyfin")
    assert "rien n'a été changé" in tool_setup.after_install("jellyfin")


def test_nextcloud_set_up_and_linked_to_jabba(vault, monkeypatch):
    monkeypatch.setattr(tool_setup.config, "NC_LINK_FILE", str(vault / "nextcloud-jabba.json"))
    monkeypatch.setattr(tool_setup.config, "NC_APP_PASSWORD", "")
    monkeypatch.setattr(tool_setup.config, "NC_USER", "")
    monkeypatch.setattr(tool_setup.activity_log, "record", lambda *a, **k: None)
    occ = []

    def fake_occ(arguments, **k):
        occ.append(arguments)
        if arguments[0] == "status":
            return 0, 'Nextcloud\n{"installed":true}', ""
        if arguments[0] == "files_external:list":
            return 0, json.dumps([{"mount_point": "/Films et séries"}]), ""
        return 0, "", ""

    monkeypatch.setattr(tool_setup.commands, "occ", fake_occ)
    monkeypatch.setattr(tool_setup.nextcloud_settings.commands, "occ", fake_occ)
    requests = []

    def fake_get(url, params=None, headers=None, auth=None, timeout=None):
        requests.append((url, auth, headers))
        return httpx.Response(
            200, json={"ocs": {"data": {"apppassword": "appli-123"}}}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(tool_setup.httpx, "get", fake_get)
    word = tool_credentials.password_for("nextcloud")
    assert tool_setup.after_install("nextcloud").startswith("Nextcloud réglé")
    assert ["background:cron"] in occ and ["app:enable", "files_external"] in occ
    created_items = [a for a in occ if a[0] == "files_external:create"]
    assert [a[1] for a in created_items] == ["Téléchargements"]  # « Films et séries » existait déjà
    assert ["config:system:set", "trusted_proxies", "0", "--value=172.16.0.0/12"] in occ
    assert requests[0][1] == ("rob", word) and requests[0][2] == {"OCS-APIRequest": "true"}
    link = json.loads((vault / "nextcloud-jabba.json").read_text())
    assert link == {"utilisateur": "rob", "mot_de_passe_appli": "appli-123"}
    assert (vault / "nextcloud-jabba.json").stat().st_mode & 0o077 == 0
    assert tool_setup.config.NC_APP_PASSWORD == "appli-123"  # l'écran Fichiers marche sans redémarrer


def test_nextcloud_still_installing(vault, monkeypatch):
    monkeypatch.setattr(tool_setup, "_wait", lambda ready, **k: False)
    assert "s'installe encore" in tool_setup.after_install("nextcloud")


def test_other_tools_have_nothing_to_set_up(vault):
    assert tool_setup.after_install("homebox") == ""


def test_new_address_accepted_by_nextcloud(monkeypatch):
    monkeypatch.setattr(tool_setup.config, "DEMO", False)
    additions = []
    monkeypatch.setattr(
        tool_setup.nextcloud_settings, "add_to_system_list", lambda key, value: additions.append((key, value))
    )
    tool_setup.after_address("nextcloud", "https://nextcloud-1.tail1234.ts.net")
    tool_setup.after_address("jellyfin", "https://jellyfin.tail1234.ts.net")
    assert additions == [("trusted_domains", "nextcloud-1.tail1234.ts.net")]


def test_reinstall_keeps_the_settings(tmp_path):
    """Un .env déjà là (Nextcloud installé à la main, ou retiré puis réinstallé) : gardé, et le script le dit."""
    dossier = tmp_path / "nextcloud"
    dossier.mkdir()
    (dossier / ".env").write_text("NC_ADMIN_USER=rob\nNC_ADMIN_PASSWORD='celui-d-avant'\n")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "docker").write_text("#!/bin/sh\ntrue\n")
    (fake / "docker").chmod(0o755)
    commande, _ = actions.build("installer_outil_option", {"outil": "nextcloud"})
    commande[3] = str(dossier)
    commande[6] = ""
    output = subprocess.run(
        commande,
        input="nouveau-mot-de-passe-123",
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"},
    )
    assert output.returncode == 0 and "REGLAGES_GARDES" in output.stdout
    assert "celui-d-avant" in (dossier / ".env").read_text()  # rien d'écrasé


def test_unneeded_drawn_password_forgotten(vault, monkeypatch):
    from services import background_tasks, optional_tools

    monkeypatch.setattr(background_tasks.config, "DEMO", False)
    monkeypatch.setattr(background_tasks.activity_log, "record", lambda *a, **k: None)
    monkeypatch.setattr(
        background_tasks.threading, "Thread", lambda target, daemon: type("T", (), {"start": staticmethod(target)})()
    )
    monkeypatch.setattr(background_tasks.commands, "run_action", lambda *a, **k: (0, "REGLAGES_GARDES\n", ""))
    monkeypatch.setattr(tool_setup, "after_install", lambda *a: "")
    monkeypatch.setattr(optional_tools.tool_addresses, "after_install", lambda *a: "")
    optional_tools.install("jellyfin", "rob")
    assert tool_credentials.read("jellyfin") is None  # Identifiants ne montre pas un mot de passe qui ne marche pas
    # Première installation (rien de gardé) : le mot de passe reste.
    monkeypatch.setattr(background_tasks.commands, "run_action", lambda *a, **k: (0, "installé\n", ""))
    optional_tools.install("jellyfin", "rob")
    assert tool_credentials.read("jellyfin")["identifiant"] == "rob"


def test_without_a_jabba_account_nothing_is_set_up(vault, monkeypatch):
    """Jellyfin ou Nextcloud installé à la main avant : Jabba ne touche pas à leurs comptes."""
    monkeypatch.setattr(tool_setup.httpx, "get", lambda *a, **k: pytest.fail("aucun appel attendu"))
    assert tool_setup.after_install("jellyfin") == ""


def test_removing_nextcloud_removes_its_background_job():
    commande, _ = actions.build("retirer_outil_option", {"outil": "nextcloud"})
    assert commande[-1] == catalog.NEXTCLOUD_CRON_REMOVE and "rm " not in " ".join(commande)
    commande, _ = actions.build("retirer_outil_option", {"outil": "jellyfin"})
    assert commande[-1] == ""


def test_trash_kept_30_days_unless_the_admin_chose(monkeypatch):
    from services import nextcloud_settings

    setting = {}

    def occ(args, **k):
        if args[0] == "config:system:get":
            return (0, setting["v"] + "\n", "") if "v" in setting else (1, "", "")
        if args[0] == "config:system:set":
            setting["v"] = args[2].removeprefix("--value=")
        return 0, "", ""

    monkeypatch.setattr(nextcloud_settings.commands, "occ", occ)
    assert nextcloud_settings.keep_deleted_files() and setting["v"] == "30, 60"  # réglage d'origine : remplacé
    setting["v"] = "auto"
    assert nextcloud_settings.keep_deleted_files() and setting["v"] == "30, 60"
    setting["v"] = "7, auto"  # choisi par l'admin : jamais écrasé
    assert not nextcloud_settings.keep_deleted_files() and setting["v"] == "7, auto"
    monkeypatch.setattr(nextcloud_settings.commands, "occ", lambda args, **k: (1, "Nextcloud ne répond pas", ""))
    assert not nextcloud_settings.keep_deleted_files()  # Nextcloud arrêté : rien tenté
