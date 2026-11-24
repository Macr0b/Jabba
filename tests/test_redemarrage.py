"""Redémarrage après une mise à jour : automatique à 4 h ou à la main, au choix."""

import subprocess

import pytest

from services import restart_policy


def test_lire_le_choix():
    assert restart_policy.interpret("auto\n", False) == {"mode": "auto", "choisi": True}
    assert restart_policy.interpret("manuel\n", True) == {"mode": "manuel", "choisi": True}
    # Pas encore de choix (installation d'avant 0.13) : le minuteur en place décide.
    assert restart_policy.interpret("", True) == {"mode": "auto", "choisi": False}
    assert restart_policy.interpret(None, False) == {"mode": "manuel", "choisi": False}
    assert restart_policy.interpret("n'importe quoi", True)["choisi"] is False


def test_commande_valide_et_bornee():
    for mode in restart_policy.MODES:
        commande = restart_policy.apply_command(mode)
        assert commande[:2] == ["sh", "-c"] and subprocess.run(["sh", "-n", "-c", commande[2]]).returncode == 0
        assert commande[4:7] == [mode, "/etc/jabba/redemarrage", "jabba-redemarrage-auto"]
        assert "ExecCondition=/bin/sh -c 'grep -qx auto /etc/jabba/redemarrage'" in commande[7]
        assert 'systemctl enable --now "$3.timer"' in commande[2]
    for mauvais in ("", "Auto", "auto; reboot", "manuel\nreboot"):
        with pytest.raises(ValueError):
            restart_policy.apply_command(mauvais)


def test_ne_remplace_pas_un_redemarrer_existant():
    assert "if [ ! -x /usr/local/sbin/redemarrer ]; then" in restart_policy.apply_command("manuel")[2]


def test_messages():
    assert "4 h" in restart_policy.message(True)
    assert "4 h" not in restart_policy.message(False) and "devant" in restart_policy.message(False)
