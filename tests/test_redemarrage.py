"""Redémarrage après une mise à jour : automatique à 4 h ou à la main, au choix."""
import subprocess

import pytest

import redemarrage


def test_lire_le_choix():
    assert redemarrage.lire("auto\n", False) == {"mode": "auto", "choisi": True}
    assert redemarrage.lire("manuel\n", True) == {"mode": "manuel", "choisi": True}
    # Pas encore de choix (installation d'avant 0.13) : le minuteur en place décide.
    assert redemarrage.lire("", True) == {"mode": "auto", "choisi": False}
    assert redemarrage.lire(None, False) == {"mode": "manuel", "choisi": False}
    assert redemarrage.lire("n'importe quoi", True)["choisi"] is False


def test_script_valide_et_borne():
    for mode in redemarrage.MODES:
        s = redemarrage.script(mode)
        assert subprocess.run(["sh", "-n", "-c", s]).returncode == 0
        assert f"echo {mode} > /etc/jabba/redemarrage" in s
        assert "ExecCondition=/bin/sh -c 'grep -qx auto /etc/jabba/redemarrage'" in s
        assert "enable --now jabba-redemarrage-auto.timer" in s
    for mauvais in ("", "Auto", "auto; reboot", "manuel\nreboot"):
        with pytest.raises(ValueError):
            redemarrage.script(mauvais)


def test_script_ne_remplace_pas_un_redemarrer_existant():
    s = redemarrage.script("manuel")
    assert "if [ ! -x /usr/local/sbin/redemarrer ]; then" in s


def test_messages():
    assert "4 h" in redemarrage.message(True)
    assert "4 h" not in redemarrage.message(False) and "devant" in redemarrage.message(False)
