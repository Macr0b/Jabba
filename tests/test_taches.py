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
from conftest import EN_ROOT

from host import actions


def test_le_script_passe_par_un_fichier():
    script = 'L="a b"; echo "${L% *}"'
    commande, _ = actions._background("jabba-test-1", script, "bash", ("Nice=10",))
    assert commande[:2] == ["sh", "-c"] and "${" not in commande[2]  # rien que systemd puisse remplacer
    assert commande[3:6] == ["tache", actions.TASKS_DIR, "jabba-test-1"]
    assert commande[6] == 'rm -f "$0"\n' + script  # le script arrive en argument, intact
    assert commande[7:] == ["-p", "Nice=10", "bash"]
    assert 'systemd-run --unit="$U" --collect "$@" "$D/$U.sh"' in commande[2]


def test_nom_de_tache_verifie():
    for unite in ("x; reboot", "jabba-maj-", "../jabba-maj-1"):
        with pytest.raises(actions.ActionError):
            actions._task_unit(unite)


systemd_present = shutil.which("systemd-run") and os.path.isdir("/run/systemd/system")


@pytest.mark.skipif(not (EN_ROOT and systemd_present), reason="nécessite systemd et root (fait sur GitHub)")
def test_vrai_systemd_run(tmp_path):
    """Avec le vrai systemd : ${…} arrive intact au shell, et le fichier du script s'efface."""
    os.chmod(tmp_path, 0o755)
    sortie = tmp_path / "sortie"
    unite = "jabba-test-" + str(uuid.uuid4().int)[:8]
    commande, _ = actions._background(unite, f'L="jabba v1.2.3 abc"\necho "${{L% *}}|${{L##* }}" > {sortie}\n')
    texte = commande[2].replace("--collect", "--wait --collect")
    subprocess.run(["sh", "-c", texte, *commande[3:]], check=True, capture_output=True)
    assert sortie.read_text().strip() == "jabba v1.2.3|abc"
    assert not os.path.exists(f"{actions.TASKS_DIR}/{unite}.sh")
