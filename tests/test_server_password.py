"""Vérification du mot de passe du serveur (empreintes de /etc/shadow), sans le module « crypt »."""

import subprocess

import pytest
from fake_host_service import fake_service

from host import actions, system_crypt
from security import server_password


def _openssl_fingerprint(password, salt, option):
    return subprocess.run(
        ["openssl", "passwd", option, "-salt", salt, password], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.mark.parametrize("option", ["-6", "-5", "-1"])
def test_classic_formats(option):
    """SHA-512, SHA-256 et MD5 : même empreinte que celle calculée par openssl."""
    fingerprint = _openssl_fingerprint("Mot de passe é 123", "selsel12", option)
    assert system_crypt.hash_with_setting("Mot de passe é 123", fingerprint) == fingerprint
    assert system_crypt.hash_with_setting("mauvais", fingerprint) != fingerprint


def test_yescrypt_debian_format():
    """yescrypt ($y$), le format de Debian depuis la version 11 : une empreinte se vérifie avec elle-même."""
    setting = "$y$j9T$F5Jx5fExrKuPp53xLKQ..1$"
    fingerprint = system_crypt.hash_with_setting("secret", setting)
    if fingerprint is None:
        pytest.skip("libcrypt sans yescrypt sur cette machine")
    assert fingerprint.startswith("$y$")
    assert system_crypt.hash_with_setting("secret", fingerprint) == fingerprint
    assert system_crypt.hash_with_setting("Secret", fingerprint) != fingerprint


def test_invalid_hash():
    assert system_crypt.hash_with_setting("x", "$9$inconnu$") is None


def test_the_check_returns_only_yes_or_no():
    """Le service compare lui-même : l'empreinte de /etc/shadow ne sort pas, le mot de passe ne va pas au serveur."""
    fingerprint = _openssl_fingerprint("Bon-mot-de-passe", "abcdefgh", "-6")
    was_read = (0, f"rob:{fingerprint}:19000:0:99999:7:::\n", "")
    nom = "verifier_mot_de_passe_serveur"
    assert actions.host_input(nom, {"user": "rob"}, "Bon-mot-de-passe") is None
    assert actions.finish(nom, was_read, "Bon-mot-de-passe") == (0, "ok", "")
    code, output, error = actions.finish(nom, was_read, "Mauvais")
    assert code == 1 and fingerprint not in output + error
    assert actions.finish(nom, (0, "rob:!:19000::::::\n", ""), "Bon-mot-de-passe")[0] == 2
    assert actions.finish(nom, (5, "", "Compte refusé"), "x")[0] == 5


def test_check_by_the_server(monkeypatch):
    fingerprint = _openssl_fingerprint("Bon-mot-de-passe", "abcdefgh", "-6")
    seen = []

    def fake_host(commande, timeout=30, input_text=None):
        seen.append((commande, input_text))
        return 0, f"rob:{fingerprint}:19000:0:99999:7:::\n", ""

    fake_service(monkeypatch, fake_host)
    monkeypatch.setattr(server_password, "_failures", {})
    assert server_password.check_password("rob", "Bon-mot-de-passe")
    assert not server_password.check_password("rob", "Mauvais")
    assert all(entry is None for _, entry in seen)  # le mot de passe n'est jamais envoyé au serveur
    assert server_password._failures and len(next(iter(server_password._failures.values()))) == 1
    assert not server_password.check_password("root; reboot", "Bon-mot-de-passe")  # compte refusé, rien lancé
    assert len(seen) == 2


def test_only_a_human_account(tmp_path):
    """root ou un compte de service ne peuvent être ni vérifiés ni changés."""
    commande, _ = actions.build("changer_mot_de_passe_serveur", {"user": "root"})
    output = subprocess.run(
        ["sh", "-c", commande[2].replace("exec chpasswd", "echo change"), *commande[3:]], capture_output=True, text=True
    )
    assert output.returncode == 5 and "change" not in output.stdout
    for account in ("Root", "-x", "a b", "rob:x"):
        with pytest.raises(actions.ActionError):
            actions.build("verifier_mot_de_passe_serveur", {"user": account})


def test_change_through_the_service(monkeypatch):
    seen = []
    fake_service(
        monkeypatch,
        lambda commande, timeout=30, input_text=None: seen.append((commande, input_text)) or (0, "", ""),
    )
    monkeypatch.setenv("JABBA_VERIF_FUITES", "0")
    server_password.change_password("rob", "Une phrase de passe bien longue")
    commande, entry = seen[0]
    assert commande[:2] == ["sh", "-c"] and "chpasswd" in commande[2] and commande[-1] == "rob"
    assert entry == "rob:Une phrase de passe bien longue\n"
    assert "Une phrase" not in " ".join(commande)  # jamais dans la liste des processus
    with pytest.raises(actions.ActionError):
        actions.host_input("changer_mot_de_passe_serveur", {"user": "rob"}, "a\nroot:x")


def test_lockout_per_person(monkeypatch):
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
