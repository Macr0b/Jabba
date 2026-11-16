"""Tâches de fond sur le serveur (mise à jour, sauvegarde) : le script n'est jamais déformé par systemd.

systemd-run remplace lui-même ${…} dans sa ligne de commande, avant le shell : un script passé
par « sh -c » y perdait ses ${LIGNE% *}, et la vérification de signature échouait toujours
(c'est arrivé : v0.11.0 → v0.12.0). Le script passe donc par un fichier.
"""

import os
import re
import shutil
import subprocess
import uuid

import pytest

import glob

from conftest import APP, EN_ROOT
from host import commands


def test_le_script_passe_par_un_fichier(monkeypatch):
    appels = []
    monkeypatch.setattr(
        commands,
        "run_on_host",
        lambda command, timeout=30, input_text=None: (appels.append((command, input_text)), (0, "", ""))[1],
    )
    script = 'L="a b"; echo "${L% *}"'
    commands.start_on_host("jabba-test-1", script, "bash", ("Nice=10",))
    (ecrire, contenu), (lancer, _) = appels
    assert contenu.endswith(script) and contenu.startswith('rm -f "$0"')
    assert "$" not in " ".join(lancer)  # rien que systemd puisse remplacer
    assert lancer == [
        "systemd-run",
        "--unit=jabba-test-1",
        "--collect",
        "-p",
        "Nice=10",
        "bash",
        f"{commands.TASKS_DIR}/jabba-test-1.sh",
    ]


def test_nom_de_tache_verifie():
    with pytest.raises(ValueError):
        commands.start_on_host("x; reboot", "true")


def test_aucun_script_en_ligne_de_commande_de_systemd_run():
    """Garde-fou : plus de « systemd-run … sh -c <script> » avec ${…} dans le code."""
    source = "".join(open(f).read() for f in glob.glob(os.path.join(APP, "**", "*.py"), recursive=True))
    for appel in re.findall(r'hote\(\["systemd-run".*?\]\)', source, re.S):
        assert "${" not in appel and "script" not in appel, appel


systemd_present = shutil.which("systemd-run") and os.path.isdir("/run/systemd/system")


@pytest.mark.skipif(not (EN_ROOT and systemd_present), reason="nécessite systemd et root (fait sur GitHub)")
def test_vrai_systemd_run(tmp_path):
    """Avec le vrai systemd : ${…} arrive intact au shell."""
    os.chmod(tmp_path, 0o755)
    sortie = tmp_path / "sortie"
    unite = "jabba-test-" + uuid.uuid4().hex[:8]
    os.makedirs(commands.TASKS_DIR, exist_ok=True)
    with open(f"{commands.TASKS_DIR}/{unite}.sh", "w") as f:
        f.write(f'rm -f "$0"\nL="jabba v1.2.3 abc"\necho "${{L% *}}|${{L##* }}" > {sortie}\n')
    cmd = commands.task_command(unite)
    subprocess.run([cmd[0], "--wait"] + cmd[1:], check=True, capture_output=True)
    assert sortie.read_text().strip() == "jabba v1.2.3|abc"
    assert not os.path.exists(f"{commands.TASKS_DIR}/{unite}.sh")
