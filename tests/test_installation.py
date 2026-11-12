"""Le script d'installation : Dockge hors du Wi-Fi (point 3), pas de groupe docker (point 2)."""
import os
import re
import subprocess

import yaml

from conftest import RACINE

SCRIPT = open(os.path.join(RACINE, "installer.sh")).read()


def test_syntaxe():
    assert subprocess.run(["bash", "-n", os.path.join(RACINE, "installer.sh")]).returncode == 0


def test_dockge_n_ecoute_que_sur_le_serveur():
    bloc = re.search(r"cat > /opt/dockge/compose.yaml <<'FIN'\n(.*?)\nFIN", SCRIPT, re.S).group(1)
    ports = yaml.safe_load(bloc)["services"]["dockge"]["ports"]
    assert ports == ["127.0.0.1:5001:5001"]
    assert "tailscale serve --bg --https=5001 http://127.0.0.1:5001" in SCRIPT
    assert "dockge.kuma.pet" not in SCRIPT            # plus de fichier téléchargé qui ouvrirait le port à tous
    assert ".local:5001" not in SCRIPT.split("Anciennes installations")[0].split("cat > .env")[1]


def test_compte_du_serveur_hors_du_groupe_docker():
    assert not re.search(r"usermod\s+-aG\s+\S*docker", SCRIPT)
    assert 'gpasswd -d "$MOI" docker' in SCRIPT


def test_porte_du_wifi_avec_le_compte_du_serveur():
    assert "JABBA_UID=" in SCRIPT


def _redemarrer(tmp_path, noyaux, kexec_ok=True):
    """Le script « redemarrer » de l'installation, avec de faux /boot, kexec et systemctl."""
    import re as re_
    bloc = re_.search(r"cat > /usr/local/sbin/redemarrer <<'FIN'\n(.*?)\nFIN", SCRIPT, re_.S).group(1)
    boot = tmp_path / "boot"
    boot.mkdir()
    for v in noyaux:
        (boot / f"vmlinuz-{v}").write_text("")
        (boot / f"initrd.img-{v}").write_text("")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "log"
    for nom, code in (("kexec", 0 if kexec_ok else 1), ("systemctl", 0), ("logger", 0)):
        (bin_ / nom).write_text(f'#!/bin/sh\necho "{nom} $*" >> {log}\nexit {code}\n')
        os.chmod(bin_ / nom, 0o755)
    script = tmp_path / "redemarrer"
    script.write_text(bloc.replace("/boot/", f"{boot}/"))
    subprocess.run(["sh", str(script)], env={"PATH": f"{bin_}:/usr/bin:/bin"}, check=True)
    return log.read_text()


def test_redemarrer_sans_bios_sur_le_noyau_le_plus_recent(tmp_path):
    log = _redemarrer(tmp_path, ["6.12.9-amd64", "6.12.48-amd64", "6.12.10-amd64"])
    assert "vmlinuz-6.12.48-amd64" in log and "initrd.img-6.12.48-amd64" in log
    assert "systemctl kexec" in log and "systemctl reboot" not in log


def test_redemarrer_repli_classique(tmp_path):
    log = _redemarrer(tmp_path, ["6.12.48-amd64"], kexec_ok=False)
    assert "systemctl reboot" in log and "systemctl kexec" not in log


def test_redemarrages_automatiques_par_jabba():
    assert 'Unattended-Upgrade::Automatic-Reboot "false"' in SCRIPT
    assert "ConditionPathExists=/run/reboot-required" in SCRIPT and "OnCalendar=*-*-* 04:00" in SCRIPT
    assert "RuntimeWatchdogSec=2min" in SCRIPT and "kernel.panic = 10" in SCRIPT
    assert "/usr/local/sbin/redemarrer" in SCRIPT.split("Redémarrer maintenant")[1]


def test_disque_de_donnees_ne_bloque_pas_le_demarrage(tmp_path):
    import re as re_
    awk = re_.search(r"awk ('\$2 == \"/srv/data\".*?\{ print \}')", SCRIPT, re_.S).group(1)
    fstab = tmp_path / "fstab"
    fstab.write_text("UUID=a /srv/data ext4 defaults 0 2\n# UUID=b /srv/data ext4 defaults 0 2\nUUID=c / ext4 defaults 0 1\n")
    sortie = subprocess.run(["sh", "-c", f"awk {awk} {fstab}"], capture_output=True, text=True, check=True).stdout
    lignes = sortie.splitlines()
    assert lignes[0].split()[3] == "defaults,nofail,x-systemd.device-timeout=30s"
    assert lignes[1] == "# UUID=b /srv/data ext4 defaults 0 2" and lignes[2] == "UUID=c / ext4 defaults 0 1"
    deux_fois = subprocess.run(["sh", "-c", f"awk {awk} {fstab} > {fstab}.2 && awk {awk} {fstab}.2"], capture_output=True, text=True).stdout
    assert deux_fois == sortie                       # relancer le script ne rajoute rien
