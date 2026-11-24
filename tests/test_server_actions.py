"""Liste fermée des actions sur le serveur (point 1 de la revue) : rien d'autre ne peut être lancé."""

import json
import os
import socket
import threading
import time

import pytest
from fake_host_service import fake_service

from host import action_common, actions, actions_system, commands, host_service


def test_known_actions():
    assert actions.build("nom_du_serveur") == (["hostname"], 10)
    assert actions.build("ejecter", {"path": "/srv/disques/R2-D2 (copie) l'été"})[0] == [
        "sh",
        "-c",
        'sync && umount "$1"',
        "ejecter",
        "/srv/disques/R2-D2 (copie) l'été",
    ]
    assert actions.build("sante_disque", {"device": "/dev/nvme0n1", "sat_bridge": True})[0][-3:] == [
        "-d",
        "sat",
        "/dev/nvme0n1",
    ]
    assert actions.build("espace_disques", {"paths": ["/", "/srv/data", "/mnt/x"]})[0][-3:] == [
        "/",
        "/srv/data",
        "/mnt/x",
    ]


@pytest.mark.parametrize(
    "nom, args",
    [
        ("rm", {}),
        ("sh", {"command": "id"}),
        ("", {}),
        ("ejecter", {"path": "/etc"}),
        ("ejecter", {"path": "/srv/../etc"}),
        ("ejecter", {"path": "/srv/disques/x/../../../etc"}),
        ("ejecter", {"path": "srv/disques/x"}),
        ("ejecter", {"path": "/srv/disques/x\n; reboot"}),
        ("ejecter", {"path": "/srv//x"}),
        ("ejecter", {}),
        ("est_monte", {"path": "/"}),
        ("sante_disque", {"device": "/dev/sda; reboot"}),
        ("sante_disque", {"device": "/etc/passwd"}),
        ("sante_disque", {"device": "/dev/zram0"}),
        ("espace_disques", {"paths": "/"}),
        ("espace_disques", {"paths": []}),
        ("espace_disques", {"paths": ["/home"]}),
        ("espace_disques", {"paths": ["/srv/x"] * 33}),
        ("nom_du_serveur", ["liste"]),
    ],
)
def test_everything_else_is_refused(nom, args):
    with pytest.raises(actions.ActionError):
        actions.build(nom, args)


def test_the_service_runs_only_the_list():
    started = []

    def fake(commande, delai, entry=None):
        started.append(commande)
        return 0, "serveur-test\n", ""

    assert host_service.handle(json.dumps({"action": "nom_du_serveur"}), fake) == {
        "code": 0,
        "output": "serveur-test\n",
        "error": "",
    }
    for request_made in [
        '{"action": "rm", "args": {}}',
        "pas du json",
        "{}",
        '{"action": "ejecter", "args": {"chemin": "/etc"}}',
        "[]",
    ]:
        answer = host_service.handle(request_made, fake)
        assert answer["code"] == host_service.REFUSED
    assert started == [["hostname"]]


def test_through_the_service_socket(monkeypatch):
    """De bout en bout : l'appli demande, le service répond par la prise Unix."""
    import tempfile

    chemin = os.path.join(tempfile.mkdtemp(dir="/tmp"), "h.sock")  # chemin court : 108 caractères au plus
    monkeypatch.setattr(host_service, "SOCKET_PATH", chemin)
    monkeypatch.setattr(host_service, "run", lambda commande, delai, entry=None: (0, " ".join(commande), ""))
    import asyncio

    loop = asyncio.new_event_loop()

    def servir():
        try:
            loop.run_until_complete(host_service.main())
        except asyncio.CancelledError:
            pass

    trail = threading.Thread(target=servir, daemon=True)
    trail.start()
    for _ in range(100):
        if os.path.exists(chemin):
            break
        time.sleep(0.02)
    assert oct(os.stat(chemin).st_mode)[-3:] == "660"
    monkeypatch.setattr(commands, "HOST_SOCKET", chemin)
    assert commands.run_action("est_monte", path="/srv/disques/R2") == (0, "mountpoint -q /srv/disques/R2", "")
    with pytest.raises(actions.ActionError):
        commands.run_action("ejecter", path="/etc")  # refusé avant même d'être envoyé
    # une demande trafiquée envoyée directement à la prise est refusée aussi
    with socket.socket(socket.AF_UNIX) as raw:
        raw.connect(chemin)
        raw.sendall(b'{"action": "sh", "args": {"c": "id"}}\n')
        assert json.loads(raw.recv(4096))["code"] == host_service.REFUSED
    with socket.socket(socket.AF_UNIX) as raw:
        raw.connect(chemin)
        raw.sendall(b"x" * (host_service.MAX_REQUEST_BYTES + 10) + b"\n")
        assert json.loads(raw.recv(4096))["code"] == host_service.REFUSED
    loop.call_soon_threadsafe(lambda: [task.cancel() for task in asyncio.all_tasks(loop)])
    trail.join(5)


def test_without_service_nothing_runs(monkeypatch):
    """Plus d'ancienne voie : sans le service, l'appli ne peut rien faire sur le serveur, et le dit."""
    monkeypatch.setattr(commands, "HOST_SOCKET", "/nulle/part.sock")
    assert commands.run_action("lire_fstab") == (1, "", commands.NO_SERVICE)


def test_service_isolated_in_compose():
    import yaml

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    services = yaml.safe_load(open(os.path.join(root, "compose.yaml")))["services"]
    host_name = services["jabba-hote"]
    assert host_name["command"] == ["python", "-m", "host.host_service"]
    assert host_name["network_mode"] == "none" and "ports" not in host_name  # aucun réseau
    assert host_name["volumes"] == ["/run/jabba:/run/jabba"]  # ne voit ni les données de l'appli, ni /srv
    assert "/run/jabba:/run/jabba" in services["jabba-app"]["volumes"]
    assert "jabba-hote" in services["jabba-app"]["depends_on"]


def test_the_app_has_no_rights_on_the_server():
    """Dernière étape : seul jabba-hote agit sur le serveur ; l'appli n'en a plus les moyens."""
    import yaml

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    jabba_app = yaml.safe_load(open(os.path.join(root, "compose.yaml")))["services"]["jabba-app"]
    assert "privileged" not in jabba_app and "pid" not in jabba_app  # ni tous les pouvoirs, ni les processus du serveur
    assert jabba_app["cap_drop"] == ["ALL"] and "no-new-privileges:true" in jabba_app["security_opt"]
    # Rien qui permette d'entrer dans le serveur ou de monter un disque.
    assert not {"SYS_ADMIN", "SYS_PTRACE", "SYS_CHROOT", "SYS_MODULE", "NET_ADMIN", "MKNOD"} & set(jabba_app["cap_add"])
    assert jabba_app["devices"] == ["${JABBA_GPU:-/dev/null}:${JABBA_GPU:-/dev/jabba-sans-gpu}"]
    assert not any("docker.sock" in str(volume) or str(volume).startswith("/:") for volume in jabba_app["volumes"])


def test_tool_gestures():
    commande, delai = actions.build(
        "outil", {"folder": "/opt/stacks/nextcloud", "gesture": "demarrer", "address": "adresse-nextcloud"}
    )
    assert commande[:2] == ["sh", "-c"] and commande[3:] == ["outil", "/opt/stacks/nextcloud", "adresse-nextcloud"]
    assert '"$1"' in commande[2] and "/opt" not in commande[2]  # le dossier n'est jamais dans le texte du script
    assert actions.build("outil", {"folder": "/opt/dockge", "gesture": "redemarrer"})[0][-1] == ""
    for args in (
        {"folder": "/opt/stacks/x", "gesture": "rm"},
        {"folder": "/etc", "gesture": "demarrer"},
        {"folder": "/opt/stacks/../../etc", "gesture": "demarrer"},
        {"folder": "/opt/stacks/x; reboot", "gesture": "demarrer"},
        {"folder": "/opt/stacks/x", "gesture": "demarrer", "address": "x; reboot"},
    ):
        with pytest.raises(actions.ActionError):
            actions.build("outil", args)
    for nom in ("", "-rf", "a b", "x;y"):
        with pytest.raises(actions.ActionError):
            actions.build("conteneur_actif", {"name": nom})
    with pytest.raises(actions.ActionError):
        actions.build("details_image", {"image": "nextcloud:latest"})


def test_nextcloud_only_the_planned_commands():
    commande, delai = actions.build("nextcloud", {"command": "user:list", "arguments": ["--output=json"]})
    assert commande[:2] == ["docker", "exec"] and commande[-2:] == ["user:list", "--output=json"] and delai == 60
    # Un texte avec « ; » ou des guillemets reste un seul argument : jamais interprété par un shell.
    commande, _ = actions.build(
        "nextcloud", {"command": "config:app:set", "arguments": ["a", "b", "--value", "x; reboot"]}
    )
    assert commande[-1] == "x; reboot" and "sh" not in commande
    for args in (
        {"command": "maintenance:mode"},
        {"command": "db:convert-type"},
        {"command": "user:list", "arguments": "--output=json"},
        {"command": "user:list", "arguments": ["a\x00b"]},
        {"command": "user:list", "delay": 999999},
    ):
        with pytest.raises(actions.ActionError):
            actions.build("nextcloud", args)


def test_password_through_standard_input():
    commande, _ = actions.build(
        "nextcloud", {"command": "user:add", "arguments": ["--password-from-env", "alice"], "password": True}
    )
    assert commande[:2] == ["sh", "-c"] and "OC_PASS" in commande
    assert actions.check_input("nextcloud", "secret-tres-long") == "secret-tres-long"
    with pytest.raises(actions.ActionError):
        actions.check_input("liste_conteneurs", "texte")  # aucune autre action ne reçoit de texte
    seen = []

    def runner(commande, delai, entry=None):
        seen.append(entry)
        return 0, "", ""

    request_made = json.dumps(
        {
            "action": "nextcloud",
            "args": {
                "command": "user:resetpassword",
                "arguments": ["--password-from-env", "alice"],
                "password": True,
            },
            "entry": "secret-tres-long",
        }
    )
    assert host_service.handle(request_made, runner)["code"] == 0 and seen == ["secret-tres-long"]
    request_made = json.dumps({"action": "espace_disques", "args": {"paths": ["/"]}, "entry": "x"})
    assert host_service.handle(request_made, runner)["code"] == host_service.REFUSED


def test_occ_goes_through_the_closed_list(monkeypatch):
    seen = []
    fake_service(
        monkeypatch,
        lambda commande, timeout=30, input_text=None: seen.append((commande, input_text)) or (0, "ok", ""),
    )
    monkeypatch.setattr(commands.config, "DEMO", False)
    assert commands.occ(["user:list", "--output=json"]) == (0, "ok", "")
    assert (
        commands.occ(["user:add", "--password-from-env", "bob"], input_text="motdepasse-long", is_password=True)[0] == 0
    )
    assert seen[1][1] == "motdepasse-long" and "motdepasse-long" not in seen[1][0]
    assert commands.occ(["maintenance:mode", "--on"])[0] == 1  # refusée, rien lancé
    assert len(seen) == 2


def test_system_actions_without_parameters():
    for nom in (
        "version_systeme",
        "noyau",
        "redemarrage_requis",
        "journal_mises_a_jour",
        "appareils_tailscale",
        "alimentation",
        "redemarrer_serveur",
        "lire_regle_redemarrage",
        "minuteur_redemarrage",
        "dossier_appli",
    ):
        commande, delai = actions.build(nom, {})
        assert commande and 0 < delai <= 60


def test_restart_rule():
    commande, _ = actions.build("regle_redemarrage", {"mode": "auto"})
    assert commande[:2] == ["sh", "-c"] and commande[4] == "auto" and 'echo "$1" > "$2"' in commande[2]
    with pytest.raises(actions.ActionError):
        actions.build("regle_redemarrage", {"mode": "auto; reboot"})


def test_app_update_key_read_by_the_service(monkeypatch):
    from services import app_update

    key = "ssh-ed25519 " + "A" * 68
    monkeypatch.setattr(app_update, "publication_key", lambda: key)
    commande, _ = actions.build(
        "installer_appli", {"folder": "/opt/jabba", "tag": "v0.14.0", "unit": "jabba-maj-1700000000"}
    )
    assert commande[:2] == ["sh", "-c"] and commande[3:6] == ["tache", action_common.TASKS_DIR, "jabba-maj-1700000000"]
    script = commande[6]
    assert key in script and "${LIGNE% *}" in script  # le script arrive intact, ${…} compris
    assert "$3" in commande[2] and "/opt/jabba" not in commande[2]  # jamais collé dans le texte du shell
    for args in (
        {"folder": "/opt/jabba", "tag": "v0.14.0; reboot", "unit": "jabba-maj-1"},
        {"folder": "/opt/../etc", "tag": "v0.14.0", "unit": "jabba-maj-1"},
        {"folder": "/opt/jabba", "tag": "v0.14.0", "unit": "sshd"},
    ):
        with pytest.raises(actions.ActionError):
            actions.build("installer_appli", args)
    monkeypatch.setattr(app_update, "publication_key", lambda: None)  # sans clé chez le service : rien ne s'installe
    with pytest.raises(actions.ActionError):
        actions.build("installer_appli", {"folder": "/opt/jabba", "tag": "v0.14.0", "unit": "jabba-maj-1"})


def test_background_task_writes_then_runs_the_script(tmp_path):
    import subprocess

    commande, _ = action_common.background("jabba-test-1", 'echo "${HOME:+ok}"')
    texte = commande[2].replace(' --unit="$unit" --collect "$@"', "").replace("exec systemd-run", "cat")
    output = subprocess.run(
        ["sh", "-c", texte, "tache", str(tmp_path), "jabba-test-1", commande[6]], capture_output=True, text=True
    )
    assert output.stdout == 'rm -f "$0"\necho "${HOME:+ok}"'
    assert (tmp_path / "jabba-test-1.sh").read_text() == output.stdout


def test_task_tracking():
    assert actions.build("etat_tache", {"unit": "jabba-maj-12"})[0][-1] == "jabba-maj-12"
    for unite in ("ssh", "jabba-maj-", "jabba-maj-1;x", "--all"):
        with pytest.raises(actions.ActionError):
            actions.build("journal_tache", {"unit": unite})


# ---------------------------------------------------------------- disques : /etc/fstab modifié par le service
FSTAB_START = "# /etc/fstab\nUUID=aaaa / ext4 defaults 0 1\nUUID=bbbb /srv/data ext4 defaults 0 2"  # sans fin de ligne


def _run_on_fake_server(tmp_path, commande):
    """Joue le script d'une action sur un faux /etc/fstab ; systemctl, umount, mkdir… ne font rien."""
    import subprocess

    tools = tmp_path / "outils"
    tools.mkdir(exist_ok=True)
    for outil, texte in {
        "systemctl": "exit 0",
        "umount": "exit 0",
        "mkdir": "exit 0",
        "rmdir": "exit 0",
        "systemd-escape": 'echo "srv-disques-x.automount"',
    }.items():
        (tools / outil).write_text("#!/bin/sh\n" + texte + "\n")
        (tools / outil).chmod(0o755)
    fstab = tmp_path / "fstab"
    script = commande[2].replace("F=/etc/fstab", f"F={fstab}", 1)
    env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}
    return subprocess.run(["sh", "-c", script, *commande[3:]], capture_output=True, text=True, env=env)


def test_disk_declared_set_then_forgotten(tmp_path):
    fstab = tmp_path / "fstab"
    fstab.write_text(FSTAB_START)
    declare = actions.build(
        "declarer_disque", {"uuid": "1234-abcd", "format": "exfat", "folder": "Mes-photos", "name": "Mes photos"}
    )[0]
    assert _run_on_fake_server(tmp_path, declare).returncode == 0
    lines = fstab.read_text().splitlines()
    assert lines[:3] == FSTAB_START.splitlines()  # rien d'autre n'a bougé
    assert lines[3] == "# Ajouté par l'appli Jabba : Mes photos"
    assert lines[4].startswith("UUID=1234-ABCD /srv/disques/Mes-photos exfat ro,uid=1000,gid=33,umask=002,nofail")
    assert (tmp_path / "fstab.jabba-bak").read_text() == FSTAB_START
    assert _run_on_fake_server(tmp_path, declare).returncode == 3  # déjà déclaré : refusé

    ecriture = actions.build("mode_disque", {"path": "/srv/disques/Mes-photos", "writable": True})[0]
    assert _run_on_fake_server(tmp_path, ecriture).returncode == 0
    line = fstab.read_text().splitlines()[4]
    assert line.split()[3].startswith("rw,") and ",ro" not in line and "gid=33,umask=002" in line
    assert fstab.read_text().splitlines()[:4] == lines[:4]
    reading = actions.build("mode_disque", {"path": "/srv/disques/Mes-photos", "writable": "oui"})[
        0
    ]  # seul True vaut écriture
    _run_on_fake_server(tmp_path, reading)
    assert fstab.read_text().splitlines()[4].split()[3].startswith("ro,")
    absent = actions.build("mode_disque", {"path": "/srv/disques/Autre", "writable": True})[0]
    before = fstab.read_text()
    assert _run_on_fake_server(tmp_path, absent).returncode == 4 and fstab.read_text() == before

    forget_ = actions.build("oublier_disque", {"path": "/srv/disques/Mes-photos", "name": "Mes photos"})[0]
    assert _run_on_fake_server(tmp_path, forget_).returncode == 0
    assert fstab.read_text() == FSTAB_START + "\n"


def test_disk_parameters_refused():
    for args in (
        {"uuid": "1234; reboot", "format": "exfat", "folder": "x", "name": "x"},
        {"uuid": "1234-abcd", "format": "btrfs", "folder": "x", "name": "x"},
        {"uuid": "1234-abcd", "format": "exfat", "folder": "../etc", "name": "x"},
        {"uuid": "1234-abcd", "format": "exfat", "folder": "a b", "name": "x"},
        {"uuid": "1234-abcd", "format": "exfat", "folder": "x", "name": "x\nUUID=1 / ext4"},
    ):
        with pytest.raises(actions.ActionError):
            actions.build("declarer_disque", args)
    for chemin in ("/srv/data", "/etc", "/srv/disques/../data", "/srv/disques/"):
        with pytest.raises(actions.ActionError):
            actions.build("oublier_disque", {"path": chemin, "name": "x"})
    with pytest.raises(actions.ActionError):
        actions.build("mode_disque", {"path": "/srv/data", "writable": False})  # le disque de données reste en écriture
    with pytest.raises(actions.ActionError):
        actions.build("disque_present", {"uuid": "-o x"})


def test_usb_disk_added_automatically(tmp_path, monkeypatch):
    from services import auto_disks

    monkeypatch.setattr(auto_disks, "DISKS_FILE", str(tmp_path / "disques.json"))
    tree = {
        "blockdevices": [
            {"type": "disk", "tran": "sata", "children": [{"uuid": "aaaa", "fstype": "ext4", "size": 10**12}]},
            {
                "type": "disk",
                "tran": "usb",
                "children": [
                    {"uuid": "c12a-0001", "fstype": "vfat", "label": "EFI", "size": 10**9},
                    {"uuid": "5e5e-0002", "fstype": "exfat", "label": "Vacances", "size": 10**11, "path": "/dev/sdb2"},
                    {"uuid": "dead-0003", "fstype": "apfs", "label": "Mac", "size": 10**11},
                ],
            },
        ]
    }
    requests = []

    def fake(action, input_text=None, **args):
        requests.append((action, args))
        if action == "arbre_disques":
            return 0, json.dumps(tree), ""
        if action == "lire_fstab":
            return 0, "UUID=aaaa / ext4 defaults 0 1\n", ""
        return 0, "", ""

    monkeypatch.setattr(commands, "run_action", fake)
    alerts = []
    auto_disks.scan(lambda: False, lambda *a: alerts.append(a[1]), lambda: [])
    additions = [args for nom, args in requests if nom == "declarer_disque"]
    assert additions == [{"uuid": "5e5e-0002", "format": "exfat", "folder": "Vacances", "name": "Vacances"}]
    actions.build("declarer_disque", additions[0])  # la demande passe la vérification du service
    assert auto_disks.known_disks() == {"Vacances": "/srv/disques/Vacances"}
    assert alerts == ["Disque illisible", "Nouveau disque : Vacances"]

    requests.clear()
    assert auto_disks.forget("Vacances")["uuid"] == "5e5e-0002"
    assert requests == [("oublier_disque", {"path": "/srv/disques/Vacances", "name": "Vacances"})]
    assert auto_disks.known_disks() == {} and "5e5e-0002" in auto_disks.read()["forgotten_items"]


# ---------------------------------------------------------------- réseau invités
def test_wifi_firewall(tmp_path):
    import subprocess

    commande, _ = actions.build("pare_feu_wifi", {"interface": "wlp2s0", "ports": [8096, 5001]})
    assert commande[3:] == ["pare-feu", "wlp2s0", "5001", "8096"]
    # Joué avec un faux iptables qui note ce qu'on lui demande.
    tools = tmp_path / "outils"
    tools.mkdir()
    log = tmp_path / "journal"
    for outil in ("iptables", "ip6tables"):
        (tools / outil).write_text(f'#!/bin/sh\necho "{outil} $*" >> {log}\ncase "$1" in -C) exit 1;; esac\nexit 0\n')
        (tools / outil).chmod(0o755)
    env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}
    assert subprocess.run(["sh", "-c", commande[2], *commande[3:]], env=env).returncode == 0
    lines = log.read_text().splitlines()
    assert "iptables -A JABBA-WIFI -i wlp2s0 -p tcp -m conntrack --ctorigdstport 5001 --ctdir ORIGINAL -j DROP" in lines
    assert "ip6tables -I DOCKER-USER -j JABBA-WIFI" in lines
    for args in (
        {"interface": "wlp2s0; reboot", "ports": [80]},
        {"interface": "wlp2s0", "ports": ["80; reboot"]},
        {"interface": "wlp2s0", "ports": [0]},
        {"interface": "wlp2s0", "ports": [True]},
        {"interface": "wlp2s0", "ports": "80"},
    ):
        with pytest.raises(actions.ActionError):
            actions.build("pare_feu_wifi", args)


# ---------------------------------------------------------------- sauvegardes
BACKUP = {"disk": "/srv/disques/Gros", "data": "/opt/stacks/jabba/donnees"}


def test_restic_read_and_browse():
    commande, _ = actions.build("restic", {**BACKUP, "gesture": "copies"})
    assert commande[:4] == [
        "env",
        "RESTIC_REPOSITORY=/srv/disques/Gros/.sauvegardes-jabba",
        "RESTIC_PASSWORD_FILE=/opt/stacks/jabba/donnees/sauvegardes.cle",
        "RESTIC_CACHE_DIR=/var/cache/restic-jabba",
    ]
    assert commande[4:] == ["restic", "--no-lock", "snapshots", "--tag", "jabba", "--json"]
    commande, _ = actions.build(
        "restic", {**BACKUP, "gesture": "parcourir", "copy": "abcd1234", "path": "/srv/data/a b"}
    )
    assert commande[-3:] == ["--json", "abcd1234", "/srv/data/a b"]


def test_restic_always_restores_alongside():
    commande, delai = actions.build(
        "restic",
        {
            **BACKUP,
            "gesture": "restaurer",
            "copy": "abcd1234",
            "path": "/srv/data/Photos/2024",
            "timestamp": "2026-11-17 01h30",
        },
    )
    assert (
        commande[4:7] == ["sh", "-c", actions_system.script("restore")] and commande[7] == actions_system.RESTORE_ROOT
    )
    assert commande[8:] == [
        "--no-lock",
        "restore",
        "abcd1234:/srv/data/Photos",
        "--target",
        actions_system.RESTORE_ROOT + "/2026-11-17 01h30 (abcd1234)",
        "--include",
        "/2024",
    ]
    assert delai == 3600
    for args in (
        {"gesture": "restaurer", "copy": "abcd1234", "path": "/srv/data/x", "timestamp": "../../etc"},
        {"gesture": "restaurer", "copy": "abcd1234", "path": "/", "timestamp": "2026-11-17 01h30"},
        {"gesture": "parcourir", "copy": "--help", "path": "/"},
        {"gesture": "parcourir", "copy": "abcd1234", "path": "relatif"},
        {"gesture": "forget", "copy": "abcd1234", "path": "/"},
        {"gesture": "copies", "disk": "/etc"},
        {"gesture": "copies", "data": "/root"},
    ):
        with pytest.raises(actions.ActionError):
            actions.build("restic", {**BACKUP, **args})


def test_start_backup_script_written_by_the_service():
    commande, _ = actions.build(
        "lancer_sauvegarde", {**BACKUP, "sources": ["fichiers", "outils"], "unit": "jabba-sauvegarde-17"}
    )
    script = commande[6]
    assert (
        "\ndisk=/srv/disques/Gros\n" in script
        and "restic backup" in script
        and "sources=(/srv/data /opt/stacks)" in script  # /etc pas demandé : pas copié
    )
    assert commande[7:] == ["-p", "Nice=10", "-p", "IOSchedulingClass=idle", "bash"]
    for args in ({"sources": ["fichiers", "/root"]}, {"sources": []}, {"sources": "fichiers"}, {"unit": "sshd"}):
        with pytest.raises(actions.ActionError):
            actions.build(
                "lancer_sauvegarde", {**BACKUP, "sources": ["fichiers"], "unit": "jabba-sauvegarde-1", **args}
            )


def test_no_direct_command_outside_the_closed_list():
    """Garde-fou : en dehors de host/, l'appli ne lance rien sur le serveur sans passer par run_action."""
    import glob

    root = os.path.join(os.path.dirname(__file__), "..", "app")
    for file in glob.glob(os.path.join(root, "**", "*.py"), recursive=True):
        if os.sep + "host" + os.sep in file:
            continue
        texte = open(file, encoding="utf-8").read()
        assert "run_on_host" not in texte and "nsenter" not in texte, file


def test_old_disk_made_writable_for_nextcloud(tmp_path):
    """Un disque exFAT déclaré avant la 0.15.1 (gid=1000) : Nextcloud (www-data, 33) ne pouvait pas y écrire.
    Le régler en écriture remet les droits partagés ; un disque ext4 garde ses options."""
    fstab = tmp_path / "fstab"
    fstab.write_text(
        "UUID=aaaa / ext4 defaults 0 1\n"
        "UUID=B1 /srv/disques/R2-D2 exfat ro,uid=1000,gid=1000,nofail,x-systemd.automount 0 0\n"
        "UUID=c2 /srv/disques/Linux ext4 ro,nofail,x-systemd.automount 0 0\n"
    )
    for chemin in ("/srv/disques/R2-D2", "/srv/disques/Linux"):
        commande = actions.build("mode_disque", {"path": chemin, "writable": True})[0]
        assert _run_on_fake_server(tmp_path, commande).returncode == 0
    lines = fstab.read_text().splitlines()
    assert lines[1].split()[3] == "rw,nofail,x-systemd.automount,uid=1000,gid=33,umask=002"
    assert lines[2].split()[3] == "rw,nofail,x-systemd.automount"
    assert lines[0] == "UUID=aaaa / ext4 defaults 0 1"
