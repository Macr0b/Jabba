"""Tâches de fond sur le serveur (mise à jour, sauvegarde) : le script n'est jamais déformé par systemd.

systemd-run remplace lui-même ${…} dans sa ligne de commande, avant le shell : un script passé
par « sh -c » y perdait ses ${LIGNE% *}, et la vérification de signature échouait toujours
(c'est arrivé : v0.11.0 → v0.12.0). Le script passe donc par un fichier, écrit par le service
privilégié (host/actions.py, _background).
"""

import os
import shutil
import subprocess
import uuid

import pytest
from conftest import AS_ROOT

from host import action_common, actions


def test_the_script_goes_through_a_file():
    script = 'L="a b"; echo "${L% *}"'
    commande, _ = action_common.background("jabba-test-1", script, "bash", ("Nice=10",))
    assert commande[:2] == ["sh", "-c"] and "${" not in commande[2]  # rien que systemd puisse remplacer
    assert commande[3:6] == ["tache", action_common.TASKS_DIR, "jabba-test-1"]
    assert commande[6] == 'rm -f "$0"\n' + script  # le script arrive en argument, intact
    assert commande[7:] == ["-p", "Nice=10", "bash"]
    assert 'exec systemd-run --unit="$unit" --collect "$@" "$tasks_dir/$unit.sh"' in commande[2]


def test_task_name_checked():
    for unite in ("x; reboot", "jabba-maj-", "../jabba-maj-1"):
        with pytest.raises(actions.ActionError):
            action_common.task_unit(unite)


systemd_available = shutil.which("systemd-run") and os.path.isdir("/run/systemd/system")


@pytest.mark.skipif(not (AS_ROOT and systemd_available), reason="nécessite systemd et root (fait sur GitHub)")
def test_real_systemd_run(tmp_path):
    """Avec le vrai systemd : ${…} arrive intact au shell, et le fichier du script s'efface."""
    os.chmod(tmp_path, 0o755)
    output = tmp_path / "sortie"
    unite = "jabba-test-" + str(uuid.uuid4().int)[:8]
    commande, _ = action_common.background(unite, f'L="jabba v1.2.3 abc"\necho "${{L% *}}|${{L##* }}" > {output}\n')
    texte = commande[2].replace("--collect", "--wait --collect")
    subprocess.run(["sh", "-c", texte, *commande[3:]], check=True, capture_output=True)
    assert output.read_text().strip() == "jabba v1.2.3|abc"
    assert not os.path.exists(f"{action_common.TASKS_DIR}/{unite}.sh")
