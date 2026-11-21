"""Le script d'installation : Dockge hors du Wi-Fi (point 3), pas de groupe docker (point 2)."""

import os
import re
import subprocess

import yaml
from conftest import RACINE

from services import restart_policy

SCRIPT = open(os.path.join(RACINE, "installer.sh")).read()


def test_syntaxe():
    assert subprocess.run(["bash", "-n", os.path.join(RACINE, "installer.sh")]).returncode == 0


def test_dockge_n_ecoute_que_sur_le_serveur():
    bloc = re.search(r"cat > /opt/dockge/compose.yaml <<'FIN'\n(.*?)\nFIN", SCRIPT, re.S).group(1)
    ports = yaml.safe_load(bloc)["services"]["dockge"]["ports"]
    assert ports == ["127.0.0.1:5001:5001"]
    assert "tailscale serve --bg --https=5001 http://127.0.0.1:5001" in SCRIPT
    assert "dockge.kuma.pet" not in SCRIPT  # plus de fichier téléchargé qui ouvrirait le port à tous
    assert ".local:5001" not in SCRIPT.split("Anciennes installations")[0].split("cat > .env")[1]


def test_compte_du_serveur_hors_du_groupe_docker():
    assert not re.search(r"usermod\s+-aG\s+\S*docker", SCRIPT)
    assert 'gpasswd -d "$MOI" docker' in SCRIPT


def test_porte_du_wifi_avec_le_compte_du_serveur():
    assert "JABBA_UID=" in SCRIPT


def _bloc(chemin):
    return re.search(r"cat > " + re.escape(chemin) + r" <<'FIN'\n(.*?)\nFIN", SCRIPT, re.S).group(1)


def _executer(script, tmp_path, contenu_choix=None):
    """Lance un bout de shell avec de faux systemctl / logger / grep sur le fichier de choix."""
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    log = tmp_path / "log"
    for nom in ("systemctl", "logger"):
        (bin_ / nom).write_text(f'#!/bin/sh\necho "{nom} $*" >> {log}\n')
        os.chmod(bin_ / nom, 0o755)
    choix = tmp_path / "redemarrage"
    if contenu_choix is not None:
        choix.write_text(contenu_choix)
    r = subprocess.run(
        ["sh", "-c", script.replace("/etc/jabba/redemarrage", str(choix))], env={"PATH": f"{bin_}:/usr/bin:/bin"}
    )
    return r.returncode, (log.read_text() if log.exists() else "")


def test_redemarrer_classique_et_note_au_journal(tmp_path):
    """Plus de kexec : il a figé le démarrage d'un serveur. « redemarrer » fait un redémarrage normal."""
    assert "kexec" not in SCRIPT
    _, log = _executer(_bloc("/usr/local/sbin/redemarrer"), tmp_path)
    assert log.splitlines() == ["logger -t redemarrer Redémarrage du serveur", "systemctl reboot"]
    assert restart_policy.RESTART_SCRIPT.strip() == _bloc("/usr/local/sbin/redemarrer").strip()


def test_redemarrage_de_4h_seulement_si_choisi(tmp_path):
    service = _bloc("/etc/systemd/system/jabba-redemarrage-auto.service")
    assert "ConditionPathExists=/run/reboot-required" in service
    condition = re.search(r"^ExecCondition=/bin/sh -c '(.*)'$", service, re.M).group(1)
    assert _executer(condition, tmp_path, "auto\n")[0] == 0
    for contenu in (
        "manuel\n",
        "",
        "automatique\n",
        None,
    ):  # tout autre contenu, ou pas de fichier : on ne redémarre pas
        assert _executer(condition, tmp_path, contenu)[0] != 0
    # Jabba réécrit exactement les mêmes unités quand on change d'avis dans Système.
    assert service.strip() == restart_policy.SERVICE.strip()
    assert _bloc("/etc/systemd/system/jabba-redemarrage-auto.timer").strip() == restart_policy.TIMER.strip()
    assert "OnCalendar=*-*-* 04:00" in restart_policy.TIMER


def test_question_du_redemarrage_a_l_installation():
    assert 'Unattended-Upgrade::Automatic-Reboot "false"' in SCRIPT
    bloc = SCRIPT.split("if [ ! -f /etc/jabba/redemarrage ]; then")[1].split("\nfi\n")[0]
    assert "</dev/tty" in bloc  # demandé une seule fois, à la personne
    assert (
        "1) echo auto > /etc/jabba/redemarrage" in bloc and "*) echo manuel > /etc/jabba/redemarrage" in bloc
    )  # par défaut : à la main
    assert "RuntimeWatchdogSec=2min" in SCRIPT and "kernel.panic = 10" in SCRIPT
    assert "systemctl reboot" in SCRIPT.split("Redémarrer maintenant")[1]


def test_veille_des_disques_internes_seulement():
    regle = [ligne for ligne in SCRIPT.splitlines() if "hdparm -B 127" in ligne]
    assert len(regle) == 1
    assert 'ENV{ID_BUS}=="ata"' in regle[0]  # jamais les boîtiers USB
    assert "/usr/bin/timeout 5 /usr/sbin/hdparm" in regle[0]  # jamais plus de 5 s au démarrage


def test_disque_de_donnees_ne_bloque_pas_le_demarrage(tmp_path):
    import re as re_

    awk = re_.search(r"awk ('\$2 == \"/srv/data\".*?\{ print \}')", SCRIPT, re_.S).group(1)
    fstab = tmp_path / "fstab"
    fstab.write_text(
        "UUID=a /srv/data ext4 defaults 0 2\n# UUID=b /srv/data ext4 defaults 0 2\nUUID=c / ext4 defaults 0 1\n"
    )
    sortie = subprocess.run(["sh", "-c", f"awk {awk} {fstab}"], capture_output=True, text=True, check=True).stdout
    lignes = sortie.splitlines()
    assert lignes[0].split()[3] == "defaults,nofail,x-systemd.device-timeout=30s"
    assert lignes[1] == "# UUID=b /srv/data ext4 defaults 0 2" and lignes[2] == "UUID=c / ext4 defaults 0 1"
    deux_fois = subprocess.run(
        ["sh", "-c", f"awk {awk} {fstab} > {fstab}.2 && awk {awk} {fstab}.2"], capture_output=True, text=True
    ).stdout
    assert deux_fois == sortie  # relancer le script ne rajoute rien


def test_workflows_github_lisibles():
    """Un fichier de workflow mal écrit et GitHub ne lance plus rien (ni tests, ni release)."""
    dossier = os.path.join(RACINE, ".github", "workflows")
    for nom in os.listdir(dossier):
        w = yaml.safe_load(open(os.path.join(dossier, nom)))
        assert w.get("jobs"), nom
        assert w.get("name"), nom


def test_cle_tailscale_a_l_installation():
    """Une clé donnée une fois : le serveur n'expire plus, et l'appli la reçoit pour les adresses des outils."""
    script = SCRIPT
    assert 'read -rsp "  Colle-la puis Entrée (Entrée seule pour passer) : " CLE_API </dev/tty' in script  # masquée
    assert "^tskey-api-[A-Za-z0-9-]{10,200}$" in script
    assert "curl -fsS -o /dev/null -H @-" in script and 'Bearer $CLE_API" https' not in script  # jamais en argument
    assert '"keyExpiryDisabled":true' in script
    assert "(umask 077 && printf" in script and "unset CLE_API" in script
