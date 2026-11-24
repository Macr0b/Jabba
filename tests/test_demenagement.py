"""Déménager le serveur : la copie sur l'ancien (script, état, fin), et la reprise par le script d'installation."""

import json
import os
import subprocess

import pytest
from conftest import RACINE

from host import actions
from services import server_move, tool_addresses

INSTALLATEUR = open(os.path.join(RACINE, "installer.sh")).read()


def faux_serveur(dossier, place=10**12, tar_rate=False):
    """De faux docker, tar, du, df… : le script tourne pour de vrai, sans toucher au système."""
    bin_ = dossier / "bin"
    bin_.mkdir()
    journal = dossier / "appels.txt"
    journal.touch()
    outils = {
        "docker": f'echo "docker $*" >> {journal}\n'
        '[ "$1" = ps ] && printf "/opt/stacks/nextcloud\\n/opt/stacks/jabba-app\\n/opt/stacks/nextcloud\\n"\ntrue',
        "tar": f'echo "tar $*" >> {journal}\n'
        + ("exit 2\n" if tar_rate else "")
        + 'while [ $# -gt 0 ]; do [ "$1" = -cpf ] && echo archive > "$2"; shift; done',
        "du": 'printf "100\\t/opt/stacks\\n"',
        "df": f'printf "Avail\\n{place}\\n"',
        "findmnt": 'case "$*" in *FSTYPE*) echo ext4;; *) echo rw,relatime;; esac',
        "mountpoint": "true",
        "mount": f'echo "mount $*" >> {journal}',
        "crontab": 'echo "*/5 * * * * cron.php"',
        "tailscale": "echo '{}'",
        "hostname": "echo serveur-test",
        "getent": "echo 'rob:x:1000:1000::/home/rob:/bin/bash'",
        "sync": "true",
    }
    for nom, corps in outils.items():
        (bin_ / nom).write_text("#!/bin/sh\n" + corps + "\n")
        (bin_ / nom).chmod(0o755)
    return {"PATH": f"{bin_}:/usr/bin:/bin"}, journal


def lancer(tmp_path, avec_fichiers, **k):
    disque = tmp_path / "disque"
    disque.mkdir()
    donnees = tmp_path / "donnees"
    donnees.mkdir()
    env, journal = faux_serveur(tmp_path, **k)
    script = server_move.move_script(str(disque), avec_fichiers, str(donnees))
    subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    etat = json.loads((donnees / "demenagement-etat.json").read_text())
    return disque / ".demenagement-jabba", etat, journal.read_text()


def test_copie_complete(tmp_path):
    dossier, etat, appels = lancer(tmp_path, avec_fichiers=True)
    assert etat["code"] == 0 and etat["etape"] == "fini"
    assert {f.name for f in dossier.iterdir()} == {
        "outils.tar",
        "fichiers.tar",
        "crontab",
        "tailscale-serve.json",
        "manifeste.json",
        "outils-arretes",
    }
    manifeste = json.loads((dossier / "manifeste.json").read_text())
    assert manifeste["serveur"] == "serveur-test" and manifeste["compte"] == "rob" and manifeste["fichiers"] is True
    # Les outils s'arrêtent (une fois chacun), jamais Jabba lui-même ; les adresses ne sont pas copiées.
    assert appels.count("nextcloud stop") == 1 and (dossier / "outils-arretes").read_text() == "/opt/stacks/nextcloud\n"
    assert "--exclude=stacks/adresse-*" in appels and "--numeric-owner" in appels
    assert " start" not in appels  # réussi : les outils restent arrêtés


def test_sur_le_disque_de_donnees_les_fichiers_ne_sont_pas_recopies(tmp_path):
    dossier, etat, appels = lancer(tmp_path, avec_fichiers=False)
    assert etat["code"] == 0 and not (dossier / "fichiers.tar").exists()
    assert json.loads((dossier / "manifeste.json").read_text())["fichiers"] is False


def test_pas_assez_de_place_rien_n_est_arrete(tmp_path):
    dossier, etat, appels = lancer(tmp_path, avec_fichiers=True, place=50)
    assert etat["code"] == 12 and etat["besoin"] >= 100 and etat["libre"] == 50
    assert " stop" not in appels and not dossier.exists()


def test_copie_ratee_les_outils_repartent(tmp_path):
    dossier, etat, appels = lancer(tmp_path, avec_fichiers=True, tar_rate=True)
    assert etat["code"] == 14 and "nextcloud start" in appels
    assert not dossier.exists() and not dossier.with_name(".demenagement-jabba.tmp").exists()


def test_actions_du_demenagement():
    commande, _ = actions.build(
        "demenager",
        {"disque": "/srv/data", "fichiers": False, "unite": "jabba-demenage-1", "donnees": "/opt/stacks/j/donnees"},
    )
    assert "jabba-demenage-1" in commande and "outils.tar" in commande[6]
    for mauvais in ({"disque": "/etc", "fichiers": False}, {"disque": "/srv/data", "fichiers": "oui"}):
        with pytest.raises(actions.ActionError):
            actions.build("demenager", {"unite": "jabba-demenage-1", "donnees": "/opt/stacks/j/donnees", **mauvais})
    fin = actions.build("fin_demenagement", {})[0]
    script = fin[6]
    assert script.index("tailscale logout") < script.index("systemctl poweroff") and "sleep 30" in script
    annuler = actions.build("annuler_demenagement", {"disque": "/srv/disques/R2"})[0]
    assert annuler[-1] == "/srv/disques/R2" and "compose --project-directory" in annuler[2]
    assert 'rm -rf "$copy" "$copy.tmp"' in annuler[2]
    with pytest.raises(actions.ActionError):
        actions.build("annuler_demenagement", {"disque": "/"})


@pytest.fixture
def carte(tmp_path, monkeypatch):
    monkeypatch.setattr(server_move, "SETTINGS_FILE", str(tmp_path / "demenagement.json"))
    monkeypatch.setattr(server_move, "STATE_FILE", str(tmp_path / "demenagement-etat.json"))
    monkeypatch.setattr(server_move.config, "DEMO", False)
    monkeypatch.setattr(server_move.files_source, "is_mounted", lambda chemin: True)
    monkeypatch.setattr(server_move.files_source, "known_disks", lambda: {"R2": "/srv/disques/R2"})
    monkeypatch.setattr(server_move.backups, "host_data_dir", lambda: "/opt/stacks/j/donnees")
    monkeypatch.setattr(server_move.backups, "is_running", lambda r: False)
    monkeypatch.setattr(server_move.activity_log, "record", lambda *a, **k: None)
    lancees = []
    monkeypatch.setattr(server_move.commands, "run_action", lambda nom, **k: lancees.append((nom, k)) or (0, "", ""))
    return tmp_path, lancees


def test_choix_du_disque_et_etats(carte):
    dossier, lancees = carte
    etat = server_move.state()
    assert etat["etat"] == "rien" and [d["fichiers"] for d in etat["disques"]] == [False, True]
    with pytest.raises(server_move.ServiceError):
        server_move.start("/srv/disques/inconnu")
    server_move.start("/srv/disques/R2")
    assert lancees[0][0] == "demenager" and lancees[0][1]["fichiers"] is True
    lance = json.loads((dossier / "demenagement.json").read_text())["lance"]
    (dossier / "demenagement-etat.json").write_text(
        json.dumps({"debut": lance, "code": 12, "besoin": 3 * 1024**3, "libre": 0})
    )
    assert server_move.state()["etat"] == "rate" and "Il faut 4 Go" in server_move.state()["erreur"]
    (dossier / "demenagement-etat.json").write_text(json.dumps({"debut": lance, "fin": lance + 60, "code": 0}))
    assert server_move.state()["etat"] == "pret"
    server_move.cancel()
    assert lancees[-1] == ("annuler_demenagement", {"disque": "/srv/disques/R2"})
    assert server_move.state()["etat"] == "rien"  # l'ancien résultat ne revient pas


def test_eteindre_libere_les_noms_tailscale(carte, monkeypatch):
    dossier, lancees = carte
    with pytest.raises(server_move.ServiceError):
        server_move.finish()  # pas de copie prête
    monkeypatch.setattr(server_move, "state", lambda: {"etat": "pret"})
    monkeypatch.setattr(
        server_move.json_files,
        "load",
        lambda chemin, defaut=None: {"nextcloud": "https://nextcloud.tailnet-test.ts.net"},
    )

    def run_action(nom, **k):
        lancees.append((nom, k))
        sortie = (
            json.dumps({"Self": {"DNSName": "serveur-test.tailnet-test.ts.net."}})
            if nom == "appareils_tailscale"
            else ""
        )
        return 0, sortie, ""

    monkeypatch.setattr(server_move.commands, "run_action", run_action)
    supprimes = []
    appareils = [
        {"id": "1", "name": "nextcloud.tailnet-test.ts.net"},
        {"id": "2", "name": "serveur-test.tailnet-test.ts.net"},
        {"id": "3", "name": "iphone.tailnet-test.ts.net"},
    ]

    def api(methode, chemin, *a):
        if methode == "GET":
            return {"devices": appareils}
        supprimes.append(chemin)

    monkeypatch.setattr(tool_addresses, "_api", api)
    assert server_move.finish() == {"ok": True, "noms_liberes": 2}
    assert lancees[0][0] == "fin_demenagement"  # l'arrêt est programmé avant de couper Tailscale
    assert supprimes == ["/device/1", "/device/2"]  # le serveur en dernier, jamais les autres appareils


def test_sans_cle_tailscale_l_arret_se_fait_quand_meme(carte, monkeypatch):
    _, lancees = carte
    monkeypatch.setattr(server_move, "state", lambda: {"etat": "pret"})
    monkeypatch.setattr(tool_addresses, "_token", lambda: None)
    assert server_move.finish()["noms_liberes"] == 0 and lancees[-1][0] == "fin_demenagement"


def test_adresses_refaites_toutes_seules(monkeypatch):
    faites = []
    monkeypatch.setattr(tool_addresses, "create_missing", lambda installes, qui: faites.append(installes))
    monkeypatch.setattr(tool_addresses, "_token", lambda: None)
    tool_addresses.watch()
    assert faites == []  # sans clé : rien
    monkeypatch.setattr(tool_addresses, "_token", lambda: "tskey-api-test")
    from services import tools

    monkeypatch.setattr(tools, "list_tools", lambda: [{"id": "nextcloud"}, {"id": "jellyfin"}])
    tool_addresses.watch()
    assert faites == [{"nextcloud", "jellyfin"}]


def test_l_installation_reprend_le_demenagement():
    bloc = INSTALLATEUR.split("# Déménagement d'un ancien serveur Jabba")[1].split("# ----")[0]
    assert "/srv/data/.demenagement-jabba/manifeste.json" in bloc and "mount -o ro" in bloc  # disque USB : lu seulement
    assert 'read -rp "  Reprendre ses comptes' in bloc  # on demande toujours avant
    assert bloc.index("outils.tar") < bloc.index("fichiers.tar") and "--numeric-owner" in bloc
    assert "rm -f /opt/stacks/jabba-app/donnees/adresses-outils.json" in bloc  # Jabba refait les adresses
    assert "/var/lib/jabba/reprise-faite" in bloc  # relancé : pas repris deux fois
    relance = INSTALLATEUR.split("# Outils repris d'un ancien serveur")[1].split("# ----")[0]
    assert "docker compose up -d" in relance and 'case "$d" in /opt/stacks/*)' in relance
    assert r'"$CIBLE_R" =~ ^http://127\.0\.0\.1:[0-9]+$' in relance  # seulement des renvois vers le serveur
    # Reprise avant Tailscale (étape 7) : la clé d'accès de l'ancien serveur est réutilisée si elle vaut encore.
    assert INSTALLATEUR.index("# Déménagement d'un ancien serveur") < INSTALLATEUR.index("CLE_API_FICHIER=")
