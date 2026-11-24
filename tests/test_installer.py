"""Le script d'installation : Dockge hors du Wi-Fi (point 3), pas de groupe docker (point 2)."""

import os
import re
import subprocess

import yaml
from conftest import ROOT

from services import restart_policy

SCRIPT = open(os.path.join(ROOT, "installer.sh")).read()


def test_syntax():
    assert subprocess.run(["bash", "-n", os.path.join(ROOT, "installer.sh")]).returncode == 0


def test_dockge_listens_only_on_the_server():
    block = re.search(r"cat > /opt/dockge/compose.yaml <<'FIN'\n(.*?)\nFIN", SCRIPT, re.S).group(1)
    ports = yaml.safe_load(block)["services"]["dockge"]["ports"]
    assert ports == ["127.0.0.1:5001:5001"]
    assert "tailscale serve --bg --https=5001 http://127.0.0.1:5001" in SCRIPT
    assert "dockge.kuma.pet" not in SCRIPT  # plus de fichier téléchargé qui ouvrirait le port à tous
    assert ".local:5001" not in SCRIPT.split("Anciennes installations")[0].split("cat > .env")[1]


def test_server_account_outside_the_docker_group():
    assert not re.search(r"usermod\s+-aG\s+\S*docker", SCRIPT)
    assert 'gpasswd -d "$MOI" docker' in SCRIPT


def test_wifi_door_runs_as_the_server_account():
    assert "JABBA_UID=" in SCRIPT


def _block(chemin):
    return re.search(r"cat > " + re.escape(chemin) + r" <<'FIN'\n(.*?)\nFIN", SCRIPT, re.S).group(1)


def _execute(script, tmp_path, choice_content=None):
    """Lance un bout de shell avec de faux systemctl / logger / grep sur le fichier de choix."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "log"
    for nom in ("systemctl", "logger"):
        (bin_dir / nom).write_text(f'#!/bin/sh\necho "{nom} $*" >> {log}\n')
        os.chmod(bin_dir / nom, 0o755)
    choice = tmp_path / "redemarrage"
    if choice_content is not None:
        choice.write_text(choice_content)
    r = subprocess.run(
        ["sh", "-c", script.replace("/etc/jabba/redemarrage", str(choice))], env={"PATH": f"{bin_dir}:/usr/bin:/bin"}
    )
    return r.returncode, (log.read_text() if log.exists() else "")


def test_plain_reboot_logged(tmp_path):
    """Plus de kexec : il a figé le démarrage d'un serveur. « redemarrer » fait un redémarrage normal."""
    assert "kexec" not in SCRIPT
    _, log = _execute(_block("/usr/local/sbin/redemarrer"), tmp_path)
    assert log.splitlines() == ["logger -t redemarrer Redémarrage du serveur", "systemctl reboot"]
    assert restart_policy.RESTART_SCRIPT.strip() == _block("/usr/local/sbin/redemarrer").strip()


def test_4am_reboot_only_if_chosen(tmp_path):
    service = _block("/etc/systemd/system/jabba-redemarrage-auto.service")
    assert "ConditionPathExists=/run/reboot-required" in service
    condition = re.search(r"^ExecCondition=/bin/sh -c '(.*)'$", service, re.M).group(1)
    assert _execute(condition, tmp_path, "auto\n")[0] == 0
    for content in (
        "manuel\n",
        "",
        "automatique\n",
        None,
    ):  # tout autre contenu, ou pas de fichier : on ne redémarre pas
        assert _execute(condition, tmp_path, content)[0] != 0
    # Jabba réécrit exactement les mêmes unités quand on change d'avis dans Système.
    assert service.strip() == restart_policy.SERVICE.strip()
    assert _block("/etc/systemd/system/jabba-redemarrage-auto.timer").strip() == restart_policy.TIMER.strip()
    assert "OnCalendar=*-*-* 04:00" in restart_policy.TIMER


def test_reboot_question_at_install():
    assert 'Unattended-Upgrade::Automatic-Reboot "false"' in SCRIPT
    block = SCRIPT.split("if [ ! -f /etc/jabba/redemarrage ]; then")[1].split("\nfi\n")[0]
    assert "</dev/tty" in block  # demandé une seule fois, à la personne
    assert (
        "1) echo auto > /etc/jabba/redemarrage" in block and "*) echo manuel > /etc/jabba/redemarrage" in block
    )  # par défaut : à la main
    assert "RuntimeWatchdogSec=2min" in SCRIPT and "kernel.panic = 10" in SCRIPT
    assert "systemctl reboot" in SCRIPT.split("Redémarrer maintenant")[1]


def test_spin_down_internal_disks_only():
    rule = [line for line in SCRIPT.splitlines() if "hdparm -B 127" in line]
    assert len(rule) == 1
    assert 'ENV{ID_BUS}=="ata"' in rule[0]  # jamais les boîtiers USB
    assert "/usr/bin/timeout 5 /usr/sbin/hdparm" in rule[0]  # jamais plus de 5 s au démarrage


def test_data_disk_never_blocks_boot(tmp_path):
    import re as re_

    awk = re_.search(r"awk ('\$2 == \"/srv/data\".*?\{ print \}')", SCRIPT, re_.S).group(1)
    fstab = tmp_path / "fstab"
    fstab.write_text(
        "UUID=a /srv/data ext4 defaults 0 2\n# UUID=b /srv/data ext4 defaults 0 2\nUUID=c / ext4 defaults 0 1\n"
    )
    output = subprocess.run(["sh", "-c", f"awk {awk} {fstab}"], capture_output=True, text=True, check=True).stdout
    lines = output.splitlines()
    assert lines[0].split()[3] == "defaults,nofail,x-systemd.device-timeout=30s"
    assert lines[1] == "# UUID=b /srv/data ext4 defaults 0 2" and lines[2] == "UUID=c / ext4 defaults 0 1"
    twice = subprocess.run(
        ["sh", "-c", f"awk {awk} {fstab} > {fstab}.2 && awk {awk} {fstab}.2"], capture_output=True, text=True
    ).stdout
    assert twice == output  # relancer le script ne rajoute rien


def test_github_workflows_readable():
    """Un fichier de workflow mal écrit et GitHub ne lance plus rien (ni tests, ni release)."""
    dossier = os.path.join(ROOT, ".github", "workflows")
    for nom in os.listdir(dossier):
        w = yaml.safe_load(open(os.path.join(dossier, nom)))
        assert w.get("jobs"), nom
        assert w.get("name"), nom


def test_tailscale_key_at_install():
    """Une clé donnée une fois : le serveur n'expire plus, et l'appli la reçoit pour les adresses des outils."""
    script = SCRIPT
    assert 'read -rsp "  Colle-la puis Entrée (Entrée seule pour passer) : " CLE_API </dev/tty' in script  # masquée
    assert "^tskey-api-[A-Za-z0-9-]{10,200}$" in script
    assert "curl -fsS -o /dev/null -H @-" in script and 'Bearer $CLE_API" https' not in script  # jamais en argument
    assert '"keyExpiryDisabled":true' in script
    assert "(umask 077 && printf" in script and "unset CLE_API" in script


def test_ups_ready_before_it_is_plugged_in():
    block = SCRIPT.split("# Onduleur branché en USB")[1].split("# ----")[0]
    assert "nut-server nut-client" in block and "driver = usbhid-ups" in block and "port = auto" in block
    assert "LISTEN 127.0.0.1 3493" in block  # jamais joignable depuis le réseau
    assert "MONITOR onduleur@localhost 1 jabba" in block and "POWERDOWNFLAG /etc/killpower" in block
    assert "Restart=always" in block  # branché plus tard : reconnu sans rien faire
    assert "grep -q '^\\[jabba\\]' /etc/nut/upsd.users" in block  # relancé : le mot de passe ne change pas
    assert "chmod 640" in block and "unset NUT_MDP" in block


def test_the_installer_passes_shellcheck():
    result = subprocess.run(
        ["shellcheck", "-s", "bash", os.path.join(ROOT, "installer.sh")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout
