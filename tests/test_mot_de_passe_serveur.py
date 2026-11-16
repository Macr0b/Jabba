"""Vérification du mot de passe du serveur (empreintes de /etc/shadow), sans le module « crypt »."""

import subprocess

import pytest

from host import system_crypt
from security import server_password


def _empreinte_openssl(mdp, sel, option):
    return subprocess.run(
        ["openssl", "passwd", option, "-salt", sel, mdp], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.mark.parametrize("option", ["-6", "-5", "-1"])
def test_formats_classiques(option):
    """SHA-512, SHA-256 et MD5 : même empreinte que celle calculée par openssl."""
    empreinte = _empreinte_openssl("Mot de passe é 123", "selsel12", option)
    assert system_crypt.hash_with_setting("Mot de passe é 123", empreinte) == empreinte
    assert system_crypt.hash_with_setting("mauvais", empreinte) != empreinte


def test_yescrypt_format_de_debian():
    """yescrypt ($y$), le format de Debian depuis la version 11 : une empreinte se vérifie avec elle-même."""
    reglage = "$y$j9T$F5Jx5fExrKuPp53xLKQ..1$"
    empreinte = system_crypt.hash_with_setting("secret", reglage)
    if empreinte is None:
        pytest.skip("libcrypt sans yescrypt sur cette machine")
    assert empreinte.startswith("$y$")
    assert system_crypt.hash_with_setting("secret", empreinte) == empreinte
    assert system_crypt.hash_with_setting("Secret", empreinte) != empreinte


def test_empreinte_invalide():
    assert system_crypt.hash_with_setting("x", "$9$inconnu$") is None


def test_verification_par_le_serveur(monkeypatch):
    empreinte = _empreinte_openssl("Bon-mot-de-passe", "abcdefgh", "-6")
    faux_hote = lambda commande: (0, f"rob:{empreinte}:19000:0:99999:7:::\n", "")
    monkeypatch.setattr(server_password, "_failures", {})
    assert server_password.check_password(faux_hote, "rob", "Bon-mot-de-passe")
    assert not server_password.check_password(faux_hote, "rob", "Mauvais")
    verrouille = lambda commande: (0, "rob:!:19000::::::\n", "")
    assert not server_password.check_password(verrouille, "rob", "Bon-mot-de-passe")


def test_blocage_par_personne(monkeypatch):
    """5 erreurs bloquent celui qui se trompe pendant 10 minutes, pas les autres admins."""
    monkeypatch.setattr(server_password, "_failures", {})
    jeton = server_password.requester.set("curieux@exemple.fr")
    try:
        for _ in range(server_password.MAX_ATTEMPTS):
            assert not server_password.is_locked()
            server_password.record_failure()
        assert server_password.is_locked()
    finally:
        server_password.requester.reset(jeton)
    jeton = server_password.requester.set("rob@exemple.fr")
    try:
        assert not server_password.is_locked()
    finally:
        server_password.requester.reset(jeton)
