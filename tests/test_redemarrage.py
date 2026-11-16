"""Redémarrage après une mise à jour : automatique à 4 h ou à la main, au choix."""
import subprocess

import pytest

from services import restart_policy


def test_lire_le_choix():
    assert restart_policy.lire("auto\n", False) == {"mode": "auto", "choisi": True}
    assert restart_policy.lire("manuel\n", True) == {"mode": "manuel", "choisi": True}
    # Pas encore de choix (installation d'avant 0.13) : le minuteur en place décide.
    assert restart_policy.lire("", True) == {"mode": "auto", "choisi": False}
    assert restart_policy.lire(None, False) == {"mode": "manuel", "choisi": False}
    assert restart_policy.lire("n'importe quoi", True)["choisi"] is False


def test_script_valide_et_borne():
    for mode in restart_policy.MODES:
        s = restart_policy.script(mode)
        assert subprocess.run(["sh", "-n", "-c", s]).returncode == 0
        assert f"echo {mode} > /etc/jabba/redemarrage" in s
        assert "ExecCondition=/bin/sh -c 'grep -qx auto /etc/jabba/redemarrage'" in s
        assert "enable --now jabba-redemarrage-auto.timer" in s
    for mauvais in ("", "Auto", "auto; reboot", "manuel\nreboot"):
        with pytest.raises(ValueError):
            restart_policy.script(mauvais)


def test_script_ne_remplace_pas_un_redemarrer_existant():
    s = restart_policy.script("manuel")
    assert "if [ ! -x /usr/local/sbin/redemarrer ]; then" in s


def test_messages():
    assert "4 h" in restart_policy.message(True)
    assert "4 h" not in restart_policy.message(False) and "devant" in restart_policy.message(False)
