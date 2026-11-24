"""Outils en option de l'Établi (Vaultwarden, Immich, Time Machine) : installation fixée côté serveur."""

import os
import subprocess

import pytest

from host import actions, catalog
from services import optional_tools, tool_credentials


def _play(tmp_path, commande, entry=""):
    """Joue le script d'installation dans un dossier à part ; docker et tailscale notent ce qu'on leur demande."""
    tools = tmp_path / "outils"
    tools.mkdir(exist_ok=True)
    log = tmp_path / "journal"
    for outil in ("docker", "tailscale"):
        (tools / outil).write_text(f'#!/bin/sh\necho "{outil} $*" >> {log}\n')
        (tools / outil).chmod(0o755)
    dossier = tmp_path / "pile"
    args = [str(dossier), *commande[4:]]
    env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}
    output = subprocess.run(["sh", "-c", commande[2], *args], input=entry, capture_output=True, text=True, env=env)
    return output, dossier, log


def test_immich_secret_drawn_and_kept(tmp_path):
    commande, delai = actions.build("installer_outil_option", {"outil": "immich"})
    assert commande[3] == "/opt/stacks/immich" and delai == 1800
    commande[6] = ""  # pas de préparation sur la machine de test (/srv/data)
    output, dossier, log = _play(tmp_path, commande)
    assert output.returncode == 0, output.stderr
    settings = (dossier / ".env").read_text()
    assert "DB_PASSWORD=" in settings and catalog.SECRET_MARK not in settings
    secret = settings.split("DB_PASSWORD=")[1].split()[0]
    assert len(secret) == 48
    assert oct((dossier / ".env").stat().st_mode)[-3:] == "600"
    assert (dossier / "compose.yaml").read_text() == catalog.IMMICH_COMPOSE
    lines = [
        line_ for line_ in log.read_text().splitlines() if not line_.startswith(("tailscale status", "tailscale ip"))
    ]
    assert lines == ["docker compose up -d", "tailscale serve --bg --https=2283 http://127.0.0.1:2283"]
    _play(tmp_path, commande)  # réinstallé : le secret (et donc la base) est gardé
    assert (dossier / ".env").read_text() == settings


def test_time_machine_password_through_standard_input(tmp_path):
    word = "Une-phrase:de+passe=a@b%c!d?e*f,g"
    commande, _ = actions.build("installer_outil_option", {"outil": "time-machine"})
    assert word not in " ".join(commande)
    commande[6] = ""
    assert actions.host_input("installer_outil_option", {"outil": "time-machine"}, word) == word
    output, dossier, log = _play(tmp_path, commande, word)
    assert output.returncode == 0, output.stderr
    assert f"PASSWORD='{word}'\n" in (dossier / ".env").read_text()  # recopié tel quel, sans interprétation
    assert "tailscale serve" not in log.read_text()  # pas de page web : pas d'adresse https


@pytest.mark.parametrize(
    "word",
    ["court", "apostrophe'xxxxxxxxxxxxx", "a\nPASSWORD=xxxxxxxxxxxxxxx", "tab\txxxxxxxxxxxxxxxxx", "x" * 129, None],
)
def test_password_refused(word):
    with pytest.raises(actions.ActionError):
        actions.host_input("installer_outil_option", {"outil": "time-machine"}, word)


def test_only_catalog_tools():
    for nom in ("dockge", "jabba-app", "../x", "", None):
        with pytest.raises(actions.ActionError):
            actions.build("installer_outil_option", {"outil": nom})
    commande, _ = actions.build("retirer_outil_option", {"outil": "vaultwarden"})
    assert commande[3:] == ["/opt/stacks/vaultwarden", "8222", ""]
    assert "rm " not in commande[2]  # retirer ne supprime jamais les données


def test_ports_limited_to_the_server():
    """Les pages web des outils ne sont joignables que par Tailscale (https), jamais directement depuis le réseau."""
    assert '"127.0.0.1:8222:80"' in catalog.VAULTWARDEN_COMPOSE
    assert '"127.0.0.1:2283:2283"' in catalog.IMMICH_COMPOSE


def test_catalog_and_memory(monkeypatch):
    monkeypatch.setattr(optional_tools.measurements, "memory", lambda: {"total": 8 * 1024**3, "used": 5 * 1024**3})
    budget = optional_tools.memory_budget(
        {"nextcloud": {"memory": 600 * 1024**2}, "jellyfin": {"memory": 400 * 1024**2}}
    )
    assert budget == {"total": 8 * 1024**3, "used": 5 * 1024**3, "tools": 1000 * 1024**2, "free": 3 * 1024**3}
    listing = {outil["id"]: outil for outil in optional_tools.catalog({"vaultwarden"})}
    assert listing["vaultwarden"]["installed"] and not listing["immich"]["installed"]
    assert listing["immich"]["memory"] == 3 * 1024**3 and "password" not in listing["time-machine"]


def test_install_as_a_long_task(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_credentials, "CREDENTIALS_FILE", str(tmp_path / "identifiants.json"))
    monkeypatch.setattr(tool_credentials.config, "DEMO", False)
    started = []
    monkeypatch.setattr(
        optional_tools.background_tasks, "start_task", lambda *a, **k: started.append(a) or {"id": "t1"}
    )
    assert optional_tools.install("vaultwarden", "rob") == {"id": "t1"}
    assert started[0][1:] == ("installer_outil_option", {"outil": "vaultwarden"}, "rob", "vaultwarden", "")
    assert tool_credentials.read("vaultwarden") is None  # son compte se crée dans Vaultwarden lui-même
    # Time Machine : rien à taper, Jabba tire un mot de passe au sort et le reprend s'il est rallumé.
    optional_tools.install("time-machine", "rob")
    word = started[1][-1]
    assert len(word) == 24 and actions.host_input("installer_outil_option", {"outil": "time-machine"}, word) == word
    optional_tools.install("time-machine", "rob")
    assert started[2][-1] == word
    assert tool_credentials.read("time-machine") == {
        "username": "timemachine",
        "password": word,
        "created_by_jabba": True,
    }
    assert (tmp_path / "identifiants.json").stat().st_mode & 0o077 == 0  # lisible par l'appli seule
    with pytest.raises(actions.ActionError):
        optional_tools.install("inconnu", "rob")


def test_paperless_tailscale_address_secrets_and_password(tmp_path):
    """Paperless : deux secrets tirés au sort (un par ligne), l'adresse Tailscale, le mot de passe de l'admin."""
    commande, _ = actions.build("installer_outil_option", {"outil": "paperless"})
    commande[6] = ""
    tools = tmp_path / "outils"
    tools.mkdir()
    (tools / "tailscale").write_text(
        '#!/bin/sh\ncase "$1" in status) printf \'{\\n  "Self": {\\n    "DNSName": "serveur-test.exemple.ts.net.",\\n  }\\n}\\n\';; esac\n'
    )
    (tools / "tailscale").chmod(0o755)
    (tools / "docker").write_text("#!/bin/sh\nexit 0\n")
    (tools / "docker").chmod(0o755)
    env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}
    word = "@MOT_DE_PASSE_ADMIN@-astucieux"  # même s'il contient la marque, il est recopié une fois
    output = subprocess.run(
        ["sh", "-c", commande[2], str(tmp_path / "pile"), *commande[4:]],
        input=word,
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert output.returncode == 0, output.stderr
    settings = dict(line.split("=", 1) for line in (tmp_path / "pile" / ".env").read_text().splitlines() if "=" in line)
    assert settings["PAPERLESS_URL"] == "https://serveur-test.exemple.ts.net:8000"
    assert settings["PAPERLESS_ADMIN_PASSWORD"] == f"'{word}'"
    assert len(settings["DB_PASSWORD"]) == 48 and settings["DB_PASSWORD"] == settings["PAPERLESS_SECRET_KEY"]


def test_new_catalog_tools():
    for outil in ("adguard-home", "paperless", "homebox"):
        commande, _ = actions.build("installer_outil_option", {"outil": outil})
        assert commande[3] == f"/opt/stacks/{outil}"
    assert (
        '"127.0.0.1:3000:3000/tcp"' in catalog.ADGUARD_COMPOSE and '"${IP_LOCALE}:53:53/tcp"' in catalog.ADGUARD_COMPOSE
    )
    assert '"127.0.0.1:8000:8000"' in catalog.PAPERLESS_COMPOSE and '"127.0.0.1:7745:7745"' in catalog.HOMEBOX_COMPOSE
    ports = [outil["port_local"] for outil in catalog.OPTIONAL_TOOLS.values() if outil["port_local"]]
    assert len(ports) == len(set(ports)) and not {8090, 5001, 9000, 8443} & set(ports)  # aucun port déjà pris
    assert catalog.OPTIONAL_TOOLS["nextcloud"]["port_local"] == 8081  # les ports de l'ancienne installation
    assert catalog.OPTIONAL_TOOLS["jellyfin"]["port_local"] == 8096  # (guide), gardés tels quels


def test_changedetection_listens_only_on_the_server():
    assert "ntfy" not in catalog.OPTIONAL_TOOLS  # retiré de l'Établi (0.16.5)
    # réseau de l'hôte, mais il n'écoute que le serveur lui-même
    assert (
        "network_mode: host" in catalog.CHANGEDETECTION_COMPOSE
        and "LISTEN_HOST: 127.0.0.1" in catalog.CHANGEDETECTION_COMPOSE
    )
    assert "ports:" not in catalog.CHANGEDETECTION_COMPOSE
    assert catalog.OPTIONAL_TOOLS["changedetection"]["port_local"] == 5005


@pytest.mark.parametrize(
    "word",
    ["Avec des espaces et é accents", "dollar$HOME-et-\\-barre", 'guillemet"xxxxxxxxxxxxx', "#dièse&et<chevrons>"],
)
def test_password_accepts_anything_but_an_apostrophe(word):
    assert actions.host_input("installer_outil_option", {"outil": "paperless"}, word) == word


def test_password_quoted_in_the_env_file(tmp_path):
    """Écrit entre apostrophes : Docker Compose ne lit ni $ ni # ni espace comme du code."""
    word = "dollar$HOME #pas-un-commentaire"
    commande, _ = actions.build("installer_outil_option", {"outil": "time-machine"})
    commande[6] = ""
    output, dossier, _ = _play(tmp_path, commande, word)
    assert output.returncode == 0, output.stderr
    assert f"PASSWORD='{word}'\n" in (dossier / ".env").read_text()


def test_adguard_listens_on_the_server_addresses(tmp_path):
    """Pas sur 0.0.0.0:53 (pris par le résolveur de Debian) : l'adresse de la maison et celle de Tailscale."""
    assert '"53:53' not in catalog.ADGUARD_COMPOSE and '"${IP_LOCALE}:53:53/udp"' in catalog.ADGUARD_COMPOSE
    assert '"${IP_TAILSCALE}:53:53/udp"' in catalog.ADGUARD_COMPOSE
    assert "ip_nonlocal_bind=1" in catalog.OPTIONAL_TOOLS["adguard-home"]["prepare"]
    commande, _ = actions.build("installer_outil_option", {"outil": "adguard-home"})
    commande[6] = ""  # pas de préparation (sysctl) pendant le test
    tools = tmp_path / "outils"
    tools.mkdir()
    (tools / "ip").write_text("#!/bin/sh\necho '1.1.1.1 via 192.168.1.254 dev enp1s0 src 192.168.1.50 uid 1000'\n")
    (tools / "tailscale").write_text('#!/bin/sh\n[ "$1" = ip ] && echo 100.64.0.7\nexit 0\n')
    (tools / "docker").write_text("#!/bin/sh\nexit 0\n")
    for outil in tools.iterdir():
        outil.chmod(0o755)
    env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}
    output = subprocess.run(
        ["sh", "-c", commande[2], str(tmp_path / "pile"), *commande[4:]],
        input="",
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert output.returncode == 0, output.stderr
    assert (tmp_path / "pile" / ".env").read_text().split() == ["IP_LOCALE=192.168.1.50", "IP_TAILSCALE=100.64.0.7"]


def test_adguard_without_tailscale_or_route(tmp_path):
    """Adresses introuvables : deux adresses locales différentes, jamais 0.0.0.0 ni deux fois la même."""
    commande, _ = actions.build("installer_outil_option", {"outil": "adguard-home"})
    commande[6] = ""
    tools = tmp_path / "outils"
    tools.mkdir()
    for nom in ("ip", "tailscale", "docker"):
        (tools / nom).write_text("#!/bin/sh\nexit 0\n")
        (tools / nom).chmod(0o755)
    env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}
    output = subprocess.run(
        ["sh", "-c", commande[2], str(tmp_path / "pile"), *commande[4:]],
        input="",
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert output.returncode == 0, output.stderr
    assert (tmp_path / "pile" / ".env").read_text().split() == ["IP_LOCALE=127.0.0.1", "IP_TAILSCALE=127.0.0.2"]


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_credentials, "CREDENTIALS_FILE", str(tmp_path / "identifiants.json"))
    monkeypatch.setattr(tool_credentials.config, "DEMO", False)
    return tmp_path / "identifiants.json"


def test_logins_noted_by_you(vault):
    """Pour un outil où tu as créé ton compte : noté une fois, relu, modifié, oublié."""
    assert tool_credentials.read("homebox") is None
    tool_credentials.save("homebox", "  rob  ", "phrase avec espaces et accents é€ '\"")
    assert tool_credentials.read("homebox") == {
        "username": "rob",
        "password": "phrase avec espaces et accents é€ '\"",
        "created_by_jabba": False,
    }
    assert vault.stat().st_mode & 0o077 == 0  # lisible par l'appli seule
    tool_credentials.save("homebox", "rob", "autre")
    assert tool_credentials.read("homebox")["password"] == "autre"
    tool_credentials.save("dockge", "rob", "dock")
    tool_credentials.forget("homebox")
    assert tool_credentials.read("homebox") is None
    assert tool_credentials.read("dockge")["password"] == "dock"  # les autres restent


def test_account_created_by_jabba_untouchable(vault):
    mot_de_passe = tool_credentials.password_for("paperless")
    for action in (
        lambda: tool_credentials.save("paperless", "admin", "x"),
        lambda: tool_credentials.forget("paperless"),
    ):
        with pytest.raises(ValueError, match="créé par Jabba"):
            action()
    assert tool_credentials.read("paperless") == {
        "username": "admin",
        "password": mot_de_passe,
        "created_by_jabba": True,
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
def test_logins_refused(vault, outil, identifiant, mot_de_passe):
    with pytest.raises(ValueError):
        tool_credentials.save(outil, identifiant, mot_de_passe)
    assert tool_credentials.read(outil) is None


def test_a_noted_account_never_becomes_a_drawn_password(vault):
    """Fichier abîmé ou ancien : une entrée qui n'est pas du bon type est ignorée, jamais renvoyée de travers."""
    vault.write_text('{"paperless": {"identifiant": "x", "mot_de_passe": "y"}, "homebox": "chaine"}')
    assert tool_credentials.read("homebox") is None
    assert tool_credentials.read("paperless") is None
    assert isinstance(tool_credentials.password_for("paperless"), str)


@pytest.mark.parametrize(
    "name", ["nextcloud", "jabba-app", "dockge", "acces-domaine", "vaultwarden", "adresse-ntfy", "../etc", "", "A"]
)
def test_leftover_removal_never_touches_a_known_tool(name):
    with pytest.raises(actions.ActionError):
        actions.build("retirer_ancien_outil", {"outil": name})


def test_leftover_tool_removed_entirely(monkeypatch):
    """ntfy, resté d'une ancienne version : « Désinstaller » retire tout, en tâche longue."""
    commande, _ = actions.build("retirer_ancien_outil", {"outil": "ntfy"})
    assert commande[-1] == "/opt/stacks/ntfy" and "docker compose down -v" in commande[2]
    started = []
    monkeypatch.setattr(
        optional_tools.background_tasks, "start_task", lambda *a, **k: started.append(a) or {"id": "t1"}
    )
    assert optional_tools.remove_leftover("ntfy", "rob") == {"id": "t1"}
    assert started[0][1:] == ("retirer_ancien_outil", {"outil": "ntfy"}, "rob", "ntfy")
    with pytest.raises(actions.ActionError):
        optional_tools.remove_leftover("nextcloud", "rob")


def test_leftover_flag_on_the_card():
    from services import tools

    ntfy = tools._tool("ntfy", {"folder": "/opt/stacks/ntfy", "containers": []})
    nextcloud = tools._tool("nextcloud", {"folder": "/opt/stacks/nextcloud", "containers": []})
    elsewhere = tools._tool("essai", {"folder": "/home/rob/essai", "containers": []})
    assert ntfy["leftover"] and not nextcloud["leftover"] and not elsewhere["leftover"]
