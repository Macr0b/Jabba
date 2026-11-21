"""Outils en option de l'Établi (Vaultwarden, Immich, Time Machine) : installation fixée côté serveur."""

import os
import subprocess

import pytest

from host import actions, catalog
from services import optional_tools, tool_credentials


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
    lignes = [
        ligne
        for ligne in journal.read_text().splitlines()
        if not ligne.startswith(("tailscale status", "tailscale ip"))
    ]
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
    assert f"PASSWORD='{mot}'\n" in (dossier / ".env").read_text()  # recopié tel quel, sans interprétation
    assert "tailscale serve" not in journal.read_text()  # pas de page web : pas d'adresse https


@pytest.mark.parametrize(
    "mot",
    ["court", "apostrophe'xxxxxxxxxxxxx", "a\nPASSWORD=xxxxxxxxxxxxxxx", "tab\txxxxxxxxxxxxxxxxx", "x" * 129, None],
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
    assert liste["immich"]["memoire"] == 3 * 1024**3 and "mot_de_passe" not in liste["time-machine"]


def test_installation_en_tache_longue(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_credentials, "CREDENTIALS_FILE", str(tmp_path / "identifiants.json"))
    monkeypatch.setattr(tool_credentials.config, "DEMO", False)
    lance = []
    monkeypatch.setattr(optional_tools.background_tasks, "start_task", lambda *a: lance.append(a) or {"id": "t1"})
    assert optional_tools.install("vaultwarden", "rob") == {"id": "t1"}
    assert lance[0][1:] == ("installer_outil_option", {"outil": "vaultwarden"}, "rob", "vaultwarden", "")
    assert tool_credentials.read("vaultwarden") is None  # son compte se crée dans Vaultwarden lui-même
    # Time Machine : rien à taper, Jabba tire un mot de passe au sort et le reprend s'il est rallumé.
    optional_tools.install("time-machine", "rob")
    mot = lance[1][-1]
    assert len(mot) == 24 and actions.host_input("installer_outil_option", {"outil": "time-machine"}, mot) == mot
    optional_tools.install("time-machine", "rob")
    assert lance[2][-1] == mot
    assert tool_credentials.read("time-machine") == {
        "identifiant": "timemachine",
        "mot_de_passe": mot,
        "cree_par_jabba": True,
    }
    assert (tmp_path / "identifiants.json").stat().st_mode & 0o077 == 0  # lisible par l'appli seule
    with pytest.raises(actions.ActionError):
        optional_tools.install("inconnu", "rob")


def test_paperless_adresse_tailscale_secrets_et_mot_de_passe(tmp_path):
    """Paperless : deux secrets tirés au sort (un par ligne), l'adresse Tailscale, le mot de passe de l'admin."""
    commande, _ = actions.build("installer_outil_option", {"outil": "paperless"})
    commande[6] = ""
    outils = tmp_path / "outils"
    outils.mkdir()
    (outils / "tailscale").write_text(
        '#!/bin/sh\ncase "$1" in status) printf \'{\\n  "Self": {\\n    "DNSName": "serveur-test.exemple.ts.net.",\\n  }\\n}\\n\';; esac\n'
    )
    (outils / "tailscale").chmod(0o755)
    (outils / "docker").write_text("#!/bin/sh\nexit 0\n")
    (outils / "docker").chmod(0o755)
    env = {**os.environ, "PATH": f"{outils}:{os.environ['PATH']}"}
    mot = "@MOT_DE_PASSE_ADMIN@-astucieux"  # même s'il contient la marque, il est recopié une fois
    sortie = subprocess.run(
        ["sh", "-c", commande[2], str(tmp_path / "pile"), *commande[4:]],
        input=mot,
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert sortie.returncode == 0, sortie.stderr
    reglages = dict(line.split("=", 1) for line in (tmp_path / "pile" / ".env").read_text().splitlines() if "=" in line)
    assert reglages["PAPERLESS_URL"] == "https://serveur-test.exemple.ts.net:8000"
    assert reglages["PAPERLESS_ADMIN_PASSWORD"] == f"'{mot}'"
    assert len(reglages["DB_PASSWORD"]) == 48 and reglages["DB_PASSWORD"] == reglages["PAPERLESS_SECRET_KEY"]


def test_nouveaux_outils_du_catalogue():
    for outil in ("adguard-home", "paperless", "homebox"):
        commande, _ = actions.build("installer_outil_option", {"outil": outil})
        assert commande[3] == f"/opt/stacks/{outil}"
    assert (
        '"127.0.0.1:3000:3000/tcp"' in catalog.ADGUARD_COMPOSE and '"${IP_LOCALE}:53:53/tcp"' in catalog.ADGUARD_COMPOSE
    )
    assert '"127.0.0.1:8000:8000"' in catalog.PAPERLESS_COMPOSE and '"127.0.0.1:7745:7745"' in catalog.HOMEBOX_COMPOSE
    ports = [outil["port_local"] for outil in catalog.OPTIONAL_TOOLS.values() if outil["port_local"]]
    assert len(ports) == len(set(ports)) and not {8090, 8081, 8096, 5001, 9000, 8443} & set(
        ports
    )  # aucun port déjà pris


def test_changedetection_n_ecoute_que_le_serveur():
    assert "ntfy" not in catalog.OPTIONAL_TOOLS  # retiré de l'Établi (0.16.5)
    # réseau de l'hôte, mais il n'écoute que le serveur lui-même
    assert (
        "network_mode: host" in catalog.CHANGEDETECTION_COMPOSE
        and "LISTEN_HOST: 127.0.0.1" in catalog.CHANGEDETECTION_COMPOSE
    )
    assert "ports:" not in catalog.CHANGEDETECTION_COMPOSE
    assert catalog.OPTIONAL_TOOLS["changedetection"]["port_local"] == 5005


@pytest.mark.parametrize(
    "mot",
    ["Avec des espaces et é accents", "dollar$HOME-et-\\-barre", 'guillemet"xxxxxxxxxxxxx', "#dièse&et<chevrons>"],
)
def test_mot_de_passe_accepte_tout_sauf_apostrophe(mot):
    assert actions.host_input("installer_outil_option", {"outil": "paperless"}, mot) == mot


def test_mot_de_passe_entre_apostrophes_dans_le_env(tmp_path):
    """Écrit entre apostrophes : Docker Compose ne lit ni $ ni # ni espace comme du code."""
    mot = "dollar$HOME #pas-un-commentaire"
    commande, _ = actions.build("installer_outil_option", {"outil": "time-machine"})
    commande[6] = ""
    sortie, dossier, _ = _jouer(tmp_path, commande, mot)
    assert sortie.returncode == 0, sortie.stderr
    assert f"PASSWORD='{mot}'\n" in (dossier / ".env").read_text()


def test_adguard_ecoute_sur_les_adresses_du_serveur(tmp_path):
    """Pas sur 0.0.0.0:53 (pris par le résolveur de Debian) : l'adresse de la maison et celle de Tailscale."""
    assert '"53:53' not in catalog.ADGUARD_COMPOSE and '"${IP_LOCALE}:53:53/udp"' in catalog.ADGUARD_COMPOSE
    assert '"${IP_TAILSCALE}:53:53/udp"' in catalog.ADGUARD_COMPOSE
    assert "ip_nonlocal_bind=1" in catalog.OPTIONAL_TOOLS["adguard-home"]["preparer"]
    commande, _ = actions.build("installer_outil_option", {"outil": "adguard-home"})
    commande[6] = ""  # pas de préparation (sysctl) pendant le test
    outils = tmp_path / "outils"
    outils.mkdir()
    (outils / "ip").write_text("#!/bin/sh\necho '1.1.1.1 via 192.168.1.254 dev enp1s0 src 192.168.1.50 uid 1000'\n")
    (outils / "tailscale").write_text('#!/bin/sh\n[ "$1" = ip ] && echo 100.64.0.7\nexit 0\n')
    (outils / "docker").write_text("#!/bin/sh\nexit 0\n")
    for outil in outils.iterdir():
        outil.chmod(0o755)
    env = {**os.environ, "PATH": f"{outils}:{os.environ['PATH']}"}
    sortie = subprocess.run(
        ["sh", "-c", commande[2], str(tmp_path / "pile"), *commande[4:]],
        input="",
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert sortie.returncode == 0, sortie.stderr
    assert (tmp_path / "pile" / ".env").read_text().split() == ["IP_LOCALE=192.168.1.50", "IP_TAILSCALE=100.64.0.7"]


def test_adguard_sans_tailscale_ni_route(tmp_path):
    """Adresses introuvables : deux adresses locales différentes, jamais 0.0.0.0 ni deux fois la même."""
    commande, _ = actions.build("installer_outil_option", {"outil": "adguard-home"})
    commande[6] = ""
    outils = tmp_path / "outils"
    outils.mkdir()
    for nom in ("ip", "tailscale", "docker"):
        (outils / nom).write_text("#!/bin/sh\nexit 0\n")
        (outils / nom).chmod(0o755)
    env = {**os.environ, "PATH": f"{outils}:{os.environ['PATH']}"}
    sortie = subprocess.run(
        ["sh", "-c", commande[2], str(tmp_path / "pile"), *commande[4:]],
        input="",
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert sortie.returncode == 0, sortie.stderr
    assert (tmp_path / "pile" / ".env").read_text().split() == ["IP_LOCALE=127.0.0.1", "IP_TAILSCALE=127.0.0.2"]


@pytest.fixture
def coffre(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_credentials, "CREDENTIALS_FILE", str(tmp_path / "identifiants.json"))
    monkeypatch.setattr(tool_credentials.config, "DEMO", False)
    return tmp_path / "identifiants.json"


def test_identifiants_notes_par_toi(coffre):
    """Pour un outil où tu as créé ton compte : noté une fois, relu, modifié, oublié."""
    assert tool_credentials.read("homebox") is None
    tool_credentials.save("homebox", "  rob  ", "phrase avec espaces et accents é€ '\"")
    assert tool_credentials.read("homebox") == {
        "identifiant": "rob",
        "mot_de_passe": "phrase avec espaces et accents é€ '\"",
        "cree_par_jabba": False,
    }
    assert coffre.stat().st_mode & 0o077 == 0  # lisible par l'appli seule
    tool_credentials.save("homebox", "rob", "autre")
    assert tool_credentials.read("homebox")["mot_de_passe"] == "autre"
    tool_credentials.save("jellyfin", "rob", "jelly")
    tool_credentials.forget("homebox")
    assert tool_credentials.read("homebox") is None
    assert tool_credentials.read("jellyfin")["mot_de_passe"] == "jelly"  # les autres restent


def test_compte_cree_par_jabba_intouchable(coffre):
    mot_de_passe = tool_credentials.password_for("paperless")
    for action in (
        lambda: tool_credentials.save("paperless", "admin", "x"),
        lambda: tool_credentials.forget("paperless"),
    ):
        with pytest.raises(ValueError, match="créé par Jabba"):
            action()
    assert tool_credentials.read("paperless") == {
        "identifiant": "admin",
        "mot_de_passe": mot_de_passe,
        "cree_par_jabba": True,
    }


@pytest.mark.parametrize(
    "outil, identifiant, mot_de_passe",
    [
        ("jabba-app", "rob", "x"),  # pas un outil où l'on se connecte
        ("adresses", "rob", "x"),
        ("../etc", "rob", "x"),
        ("homebox", "", "x"),
        ("homebox", "   ", "x"),
        ("homebox", None, "x"),
        ("homebox", "a" * 201, "x"),
        ("homebox", "rob\n", "x\nsuite"),
        ("homebox", "rob", ""),
        ("homebox", "rob", None),
        ("homebox", "rob", 1234),
        ("homebox", "rob", "x" * 501),
        ("homebox", "rob", "avec\x00nul"),
    ],
)
def test_identifiants_refuses(coffre, outil, identifiant, mot_de_passe):
    with pytest.raises(ValueError):
        tool_credentials.save(outil, identifiant, mot_de_passe)
    assert tool_credentials.read(outil) is None


def test_un_compte_note_ne_devient_pas_un_mot_de_passe_tire(coffre):
    """Fichier abîmé ou ancien : une entrée qui n'est pas du bon type est ignorée, jamais renvoyée de travers."""
    coffre.write_text('{"paperless": {"identifiant": "x", "mot_de_passe": "y"}, "homebox": "chaine"}')
    assert tool_credentials.read("homebox") is None
    assert tool_credentials.read("paperless") is None
    assert isinstance(tool_credentials.password_for("paperless"), str)
