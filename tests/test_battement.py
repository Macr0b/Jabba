"""Alerte « serveur muet » : adresse vérifiée, jamais injectée dans une commande."""
import subprocess

import pytest

import battement


@pytest.mark.parametrize("url", ["https://hc-ping.com/1b2c3d4e-0000-4000-8000-123456789abc",
                                 "https://exemple.fr:8443/ping/abc_def-1"])
def test_adresses_acceptees(url):
    assert battement.valide(url)
    assert subprocess.run(["sh", "-n", "-c", battement.installer(url)]).returncode == 0


@pytest.mark.parametrize("url", ["http://hc-ping.com/x", "https://x.fr/a b", "https://x.fr/$(reboot)", "https://x.fr/'; reboot; '",
                                 "https://x.fr/\nreboot", "ftp://x.fr", "", "https://" + "a" * 400])
def test_adresses_refusees(url):
    assert not battement.valide(url)
    with pytest.raises(ValueError):
        battement.installer(url)


def test_adresse_masquee():
    m = battement.masquer("https://hc-ping.com/1b2c3d4e-0000-4000-8000-123456789abc")
    assert m.startswith("https://hc-ping.com/…") and "1b2c3d4e" not in m
    assert battement.masquer("") is None


def test_minuteur_toutes_les_5_minutes():
    s = battement.installer("https://hc-ping.com/abc")
    assert "OnUnitActiveSec=5min" in s and "enable --now jabba-battement.timer" in s
    assert "umask 077" in s                         # l'adresse (secrète) n'est lisible que par root
    assert "systemctl disable --now jabba-battement.timer" in battement.retirer()
