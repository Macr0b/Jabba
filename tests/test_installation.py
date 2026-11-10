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
