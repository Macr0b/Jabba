"""Outils en option de l'Établi (Vaultwarden, Immich, Time Machine) : installation fixée côté serveur."""

import os
import subprocess

import pytest

from host import actions, catalog
from services import optional_tools


def _jouer(tmp_path, commande, entree=""):
    """Joue le script d'installation dans un dossier à part ; docker et tailscale notent ce qu'on leur demande."""
    outils = tmp_path / "outils"
    outils.mkdir(exist_ok=True)
    journal = tmp_path / "journal"
    for outil in ("docker", "tailscale"):
        (outils / outil).write_text(f'#!/bin/sh\necho "{outil} $*" >> {journal}\n')
        (outils / outil).chmod(0o755)
    dossier = tmp_path / "pile"
    args = [str(dossier), *commande[4:]]
    env = {**os.environ, "PATH": f"{outils}:{os.environ['PATH']}"}
    sortie = subprocess.run(["sh", "-c", commande[2], *args], input=entree, capture_output=True, text=True, env=env)
    return sortie, dossier, journal


def test_immich_secret_tire_au_sort_et_garde(tmp_path):
    commande, delai = actions.build("installer_outil_option", {"outil": "immich"})
    assert commande[3] == "/opt/stacks/immich" and delai == 1800
    commande[6] = ""  # pas de préparation sur la machine de test (/srv/data)
    sortie, dossier, journal = _jouer(tmp_path, commande)
    assert sortie.returncode == 0, sortie.stderr
    reglages = (dossier / ".env").read_text()
    assert "DB_PASSWORD=" in reglages and catalog.SECRET_MARK not in reglages
    secret = reglages.split("DB_PASSWORD=")[1].split()[0]
    assert len(secret) == 48
    assert oct((dossier / ".env").stat().st_mode)[-3:] == "600"
    assert (dossier / "compose.yaml").read_text() == catalog.IMMICH_COMPOSE
    lignes = journal.read_text().splitlines()
    assert lignes == ["docker compose up -d", "tailscale serve --bg --https=2283 http://127.0.0.1:2283"]
    _jouer(tmp_path, commande)  # réinstallé : le secret (et donc la base) est gardé
    assert (dossier / ".env").read_text() == reglages


def test_time_machine_mot_de_passe_par_l_entree_standard(tmp_path):
    mot = "Une-phrase:de+passe=a@b%c!d?e*f,g"
    commande, _ = actions.build("installer_outil_option", {"outil": "time-machine"})
    assert mot not in " ".join(commande)
    commande[6] = ""
    assert actions.host_input("installer_outil_option", {"outil": "time-machine"}, mot) == mot
    sortie, dossier, journal = _jouer(tmp_path, commande, mot)
    assert sortie.returncode == 0, sortie.stderr
    assert f"PASSWORD={mot}\n" in (dossier / ".env").read_text()  # recopié tel quel, sans interprétation
    assert "tailscale" not in journal.read_text()  # pas de page web : pas d'adresse https


@pytest.mark.parametrize(
    "mot", ["court", "avec espace dedans!!", 'guillemet"xxxxxxxxxxxxx', "dollar$HOME-xxxxxxx", "a\nPASSWORD=x", None]
)
def test_mot_de_passe_refuse(mot):
    with pytest.raises(actions.ActionError):
        actions.host_input("installer_outil_option", {"outil": "time-machine"}, mot)


def test_seulement_les_outils_du_catalogue():
    for nom in ("nextcloud", "../x", "", None):
        with pytest.raises(actions.ActionError):
            actions.build("installer_outil_option", {"outil": nom})
    commande, _ = actions.build("retirer_outil_option", {"outil": "vaultwarden"})
    assert commande[3:] == ["/opt/stacks/vaultwarden", "8222"]
    assert "rm " not in commande[2]  # retirer ne supprime jamais les données


def test_ports_limites_au_serveur():
    """Les pages web des outils ne sont joignables que par Tailscale (https), jamais directement depuis le réseau."""
    assert '"127.0.0.1:8222:80"' in catalog.VAULTWARDEN_COMPOSE
    assert '"127.0.0.1:2283:2283"' in catalog.IMMICH_COMPOSE


def test_catalogue_et_memoire(monkeypatch):
    monkeypatch.setattr(optional_tools.measurements, "memory", lambda: {"total": 8 * 1024**3, "utilise": 5 * 1024**3})
    budget = optional_tools.memory_budget(
        {"nextcloud": {"memoire": 600 * 1024**2}, "jellyfin": {"memoire": 400 * 1024**2}}
    )
    assert budget == {"total": 8 * 1024**3, "utilise": 5 * 1024**3, "outils": 1000 * 1024**2, "libre": 3 * 1024**3}
    liste = {outil["id"]: outil for outil in optional_tools.catalog({"vaultwarden"})}
    assert liste["vaultwarden"]["installe"] and not liste["immich"]["installe"]
    assert liste["immich"]["memoire"] == 3 * 1024**3 and liste["time-machine"]["mot_de_passe"]


def test_installation_en_tache_longue(monkeypatch):
    lance = []
    monkeypatch.setattr(optional_tools.background_tasks, "start_task", lambda *a: lance.append(a) or {"id": "t1"})
    assert optional_tools.install("vaultwarden", None, "rob") == {"id": "t1"}
    assert lance[0][1:] == ("installer_outil_option", {"outil": "vaultwarden"}, "rob", "vaultwarden", "")
    with pytest.raises(actions.ActionError):
        optional_tools.install("time-machine", "court", "rob")  # refusé avant de lancer quoi que ce soit
    assert len(lance) == 1
