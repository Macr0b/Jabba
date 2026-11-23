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
def coffre(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_credentials, "CREDENTIALS_FILE", str(tmp_path / "identifiants.json"))
    monkeypatch.setattr(tool_credentials.config, "DEMO", False)
    monkeypatch.setattr(tool_credentials.config, "ADMIN_USER", "rob")
    monkeypatch.setattr(tool_setup, "_wait", lambda ready, **k: ready())
    return tmp_path


def test_installation_ecrit_les_reglages(tmp_path):
    """Essai à blanc du script d'installation : chaque marque est remplacée, rien n'est laissé au hasard."""
    faux = tmp_path / "bin"
    faux.mkdir()
    scripts = {
        "docker": 'echo "$@" >> "$(dirname "$0")/appels"',
        "tailscale": '[ "$1" = status ] && echo \'  "DNSName": "serveur.tail1234.ts.net.",\'; [ "$1" = ip ] && echo 100.64.0.9; true',
        "getent": '[ "$1" = passwd ] && echo "rob:x:1000:1000::/home/rob:/bin/bash"; [ "$1" = group ] && echo "render:x:993:"; true',
        "hostname": "echo serveur",
        "ip": 'echo "1.1.1.1 via 192.168.1.1 dev en0 src 192.168.1.50 uid 0"',
    }
    for nom, corps in scripts.items():
        (faux / nom).write_text("#!/bin/sh\n" + corps + "\n")
        (faux / nom).chmod(0o755)
    env = {**os.environ, "PATH": f"{faux}:{os.environ['PATH']}"}
    lus = {}
    for outil in ("nextcloud", "jellyfin"):
        commande, _ = actions.build("installer_outil_option", {"outil": outil})
        commande[3] = str(tmp_path / outil)
        commande[6] = ""  # la préparation (dossiers de /srv, crontab) n'est pas jouée ici
        sortie = subprocess.run(commande, input="mot-de-passe-de-jabba-1234", text=True, capture_output=True, env=env)
        assert sortie.returncode == 0, sortie.stderr
        lus[outil] = (tmp_path / outil / ".env").read_text()
        assert "@" not in lus[outil]  # plus aucune marque
    nextcloud = dict(ligne.split("=", 1) for ligne in lus["nextcloud"].splitlines() if ligne)
    assert nextcloud["NC_ADMIN_USER"] == "rob" and nextcloud["NC_ADMIN_PASSWORD"] == "'mot-de-passe-de-jabba-1234'"
    assert nextcloud["DB_ROOT_PASSWORD"] != nextcloud["DB_PASSWORD"] and len(nextcloud["DB_PASSWORD"]) == 48
    assert (
        nextcloud["NC_DOMAINS"] == '"serveur serveur.tail1234.ts.net nextcloud.tail1234.ts.net 192.168.1.50 100.64.0.9"'
    )
    assert lus["jellyfin"].strip() == "TZ=Europe/Paris\nPUID=1000\nPGID=1000\nRENDER_GID=993"


def test_catalogue():
    for outil in ("nextcloud", "jellyfin"):
        entree = catalog.OPTIONAL_TOOLS[outil]
        assert entree["mot_de_passe"] and entree["identifiant"] == catalog.SERVER_USER_MARK
        assert entree["port_https"] is None  # leur adresse à part, comme avant (nextcloud.…, jellyfin.…)
        assert "mkdir -p /srv/data/media/films" in entree["preparer"]
    assert "cron.php" in catalog.OPTIONAL_TOOLS["nextcloud"]["preparer"]
    assert catalog.NEXTCLOUD_CRON.count("crontab") == 2  # relue puis réécrite : une seule ligne, jamais en double


def test_identifiant_au_nom_du_compte_du_serveur(coffre):
    tool_credentials.password_for("nextcloud")
    assert tool_credentials.read("nextcloud")["identifiant"] == "rob"


class FauxJellyfin:
    def __init__(self, fini=False):
        self.fini = fini
        self.appels = []
        self.bibliotheques = []

    def get(self, url, headers=None, timeout=None):
        chemin = url.removeprefix(tool_setup.JELLYFIN)
        self.appels.append(("GET", chemin))
        if chemin == "/System/Info/Public":
            return httpx.Response(200, json={"StartupWizardCompleted": self.fini})
        if chemin == "/Library/VirtualFolders":
            return httpx.Response(200, json=[{"Name": nom} for nom in self.bibliotheques])
        return httpx.Response(200, json={})

    def post(self, url, json=None, params=None, headers=None, timeout=None):
        chemin = url.removeprefix(tool_setup.JELLYFIN)
        self.appels.append(("POST", chemin, json, params))
        request = httpx.Request("POST", url)
        if chemin == "/Users/AuthenticateByName":
            return httpx.Response(200, json={"AccessToken": "jeton"}, request=request)
        if chemin == "/Library/VirtualFolders":
            self.bibliotheques.append(params["name"])
        return httpx.Response(204, request=request)


def test_jellyfin_assistant_et_mediatheques(coffre, monkeypatch):
    faux = FauxJellyfin()
    monkeypatch.setattr(tool_setup.httpx, "get", faux.get)
    monkeypatch.setattr(tool_setup.httpx, "post", faux.post)
    monkeypatch.setattr(tool_setup.activity_log, "record", lambda *a, **k: None)
    mot = tool_credentials.password_for("jellyfin")
    assert tool_setup.after_install("jellyfin").startswith("Jellyfin réglé")
    envois = [a[1:3] for a in faux.appels if a[0] == "POST"]
    assert ("/Startup/User", {"Name": "rob", "Password": mot}) in envois
    assert ("/Startup/RemoteAccess", {"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False}) in envois
    assert faux.bibliotheques == ["Films", "Séries"]
    # Relancé : l'assistant est fait, les médiathèques existent déjà ; rien n'est refait en double.
    faux.fini = True
    tool_setup.after_install("jellyfin")
    assert faux.bibliotheques == ["Films", "Séries"]


def test_jellyfin_regle_par_quelqu_un_d_autre(coffre, monkeypatch):
    faux = FauxJellyfin(fini=True)
    monkeypatch.setattr(tool_setup.httpx, "get", faux.get)
    monkeypatch.setattr(
        tool_setup.httpx, "post", lambda url, **k: httpx.Response(401, request=httpx.Request("POST", url))
    )
    tool_credentials.password_for("jellyfin")
    assert "rien n'a été changé" in tool_setup.after_install("jellyfin")


def test_nextcloud_regle_et_relie_a_jabba(coffre, monkeypatch):
    monkeypatch.setattr(tool_setup.config, "NC_LINK_FILE", str(coffre / "nextcloud-jabba.json"))
    monkeypatch.setattr(tool_setup.config, "NC_APP_PASSWORD", "")
    monkeypatch.setattr(tool_setup.config, "NC_USER", "")
    monkeypatch.setattr(tool_setup.activity_log, "record", lambda *a, **k: None)
    occ = []

    def faux_occ(arguments, **k):
        occ.append(arguments)
        if arguments[0] == "status":
            return 0, 'Nextcloud\n{"installed":true}', ""
        if arguments[0] == "files_external:list":
            return 0, json.dumps([{"mount_point": "/Films et séries"}]), ""
        return 0, "", ""

    monkeypatch.setattr(tool_setup.commands, "occ", faux_occ)
    monkeypatch.setattr(tool_setup.nextcloud_settings.commands, "occ", faux_occ)
    demandes = []

    def faux_get(url, params=None, headers=None, auth=None, timeout=None):
        demandes.append((url, auth, headers))
        return httpx.Response(
            200, json={"ocs": {"data": {"apppassword": "appli-123"}}}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(tool_setup.httpx, "get", faux_get)
    mot = tool_credentials.password_for("nextcloud")
    assert tool_setup.after_install("nextcloud").startswith("Nextcloud réglé")
    assert ["background:cron"] in occ and ["app:enable", "files_external"] in occ
    crees = [a for a in occ if a[0] == "files_external:create"]
    assert [a[1] for a in crees] == ["Téléchargements"]  # « Films et séries » existait déjà
    assert ["config:system:set", "trusted_proxies", "0", "--value=172.16.0.0/12"] in occ
    assert demandes[0][1] == ("rob", mot) and demandes[0][2] == {"OCS-APIRequest": "true"}
    lien = json.loads((coffre / "nextcloud-jabba.json").read_text())
    assert lien == {"utilisateur": "rob", "mot_de_passe_appli": "appli-123"}
    assert (coffre / "nextcloud-jabba.json").stat().st_mode & 0o077 == 0
    assert tool_setup.config.NC_APP_PASSWORD == "appli-123"  # l'écran Fichiers marche sans redémarrer


def test_nextcloud_qui_s_installe_encore(coffre, monkeypatch):
    monkeypatch.setattr(tool_setup, "_wait", lambda ready, **k: False)
    assert "s'installe encore" in tool_setup.after_install("nextcloud")


def test_les_autres_outils_n_ont_rien_a_regler(coffre):
    assert tool_setup.after_install("homebox") == ""


def test_nouvelle_adresse_acceptee_par_nextcloud(monkeypatch):
    monkeypatch.setattr(tool_setup.config, "DEMO", False)
    ajouts = []
    monkeypatch.setattr(
        tool_setup.nextcloud_settings, "add_to_system_list", lambda cle, valeur: ajouts.append((cle, valeur))
    )
    tool_setup.after_address("nextcloud", "https://nextcloud-1.tail1234.ts.net")
    tool_setup.after_address("jellyfin", "https://jellyfin.tail1234.ts.net")
    assert ajouts == [("trusted_domains", "nextcloud-1.tail1234.ts.net")]


def test_reinstallation_garde_les_reglages(tmp_path):
    """Un .env déjà là (Nextcloud installé à la main, ou retiré puis réinstallé) : gardé, et le script le dit."""
    dossier = tmp_path / "nextcloud"
    dossier.mkdir()
    (dossier / ".env").write_text("NC_ADMIN_USER=rob\nNC_ADMIN_PASSWORD='celui-d-avant'\n")
    faux = tmp_path / "bin"
    faux.mkdir()
    (faux / "docker").write_text("#!/bin/sh\ntrue\n")
    (faux / "docker").chmod(0o755)
    commande, _ = actions.build("installer_outil_option", {"outil": "nextcloud"})
    commande[3] = str(dossier)
    commande[6] = ""
    sortie = subprocess.run(
        commande,
        input="nouveau-mot-de-passe-123",
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{faux}:{os.environ['PATH']}"},
    )
    assert sortie.returncode == 0 and "REGLAGES_GARDES" in sortie.stdout
    assert "celui-d-avant" in (dossier / ".env").read_text()  # rien d'écrasé


def test_mot_de_passe_tire_pour_rien_oublie(coffre, monkeypatch):
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


def test_sans_compte_de_jabba_rien_n_est_regle(coffre, monkeypatch):
    """Jellyfin ou Nextcloud installé à la main avant : Jabba ne touche pas à leurs comptes."""
    monkeypatch.setattr(tool_setup.httpx, "get", lambda *a, **k: pytest.fail("aucun appel attendu"))
    assert tool_setup.after_install("jellyfin") == ""


def test_retrait_de_nextcloud_retire_sa_tache_de_fond():
    commande, _ = actions.build("retirer_outil_option", {"outil": "nextcloud"})
    assert commande[-1] == catalog.NEXTCLOUD_CRON_REMOVE and "rm " not in " ".join(commande)
    commande, _ = actions.build("retirer_outil_option", {"outil": "jellyfin"})
    assert commande[-1] == ""
