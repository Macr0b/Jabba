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

import main
from conftest import APP, EN_ROOT


def test_le_script_passe_par_un_fichier(monkeypatch):
    appels = []
    monkeypatch.setattr(main, "hote", lambda cmd, timeout=30, entree=None: (appels.append((cmd, entree)), (0, "", ""))[1])
    script = 'L="a b"; echo "${L% *}"'
    main.lancer_sur_serveur("jabba-test-1", script, "bash", ("Nice=10",))
    (ecrire, contenu), (lancer, _) = appels
    assert contenu.endswith(script) and contenu.startswith('rm -f "$0"')
    assert "$" not in " ".join(lancer)                       # rien que systemd puisse remplacer
    assert lancer == ["systemd-run", "--unit=jabba-test-1", "--collect", "-p", "Nice=10", "bash", f"{main.DOSSIER_TACHES}/jabba-test-1.sh"]


def test_nom_de_tache_verifie():
    with pytest.raises(ValueError):
        main.lancer_sur_serveur("x; reboot", "true")


def test_aucun_script_en_ligne_de_commande_de_systemd_run():
    """Garde-fou : plus de « systemd-run … sh -c <script> » avec ${…} dans le code."""
    source = open(os.path.join(APP, "main.py")).read()
    for appel in re.findall(r'hote\(\["systemd-run".*?\]\)', source, re.S):
        assert "${" not in appel and "script" not in appel, appel


systemd_present = shutil.which("systemd-run") and os.path.isdir("/run/systemd/system")


@pytest.mark.skipif(not (EN_ROOT and systemd_present), reason="nécessite systemd et root (fait sur GitHub)")
def test_vrai_systemd_run(tmp_path):
    """Avec le vrai systemd : ${…} arrive intact au shell."""
    os.chmod(tmp_path, 0o755)
    sortie = tmp_path / "sortie"
    unite = "jabba-test-" + uuid.uuid4().hex[:8]
    os.makedirs(main.DOSSIER_TACHES, exist_ok=True)
    with open(f"{main.DOSSIER_TACHES}/{unite}.sh", "w") as f:
        f.write(f'rm -f "$0"\nL="jabba v1.2.3 abc"\necho "${{L% *}}|${{L##* }}" > {sortie}\n')
    cmd = main.commande_tache(unite)
    subprocess.run([cmd[0], "--wait"] + cmd[1:], check=True, capture_output=True)
    assert sortie.read_text().strip() == "jabba v1.2.3|abc"
    assert not os.path.exists(f"{main.DOSSIER_TACHES}/{unite}.sh")
