"""Règles des nouveaux mots de passe (NIST SP 800-63B-4) : longueur, mots courants, suites, fuites."""

import hashlib

import pytest

from host import commands
from security import accounts, password_rules, server_password


def _without_leak(prefix):
    return ""


@pytest.mark.parametrize(
    "good",
    [
        "abeille camion soleil 48",  # une phrase de mots, même avec un mot courant dedans
        "La lune danse sur le toit",
        "Café-crème-à-Strasbourg",
        "x" * 3 + "Kp9!vRt2#qLm8sW",  # une petite répétition au début ne gêne pas
        "é" * 2 + "ñ€漢字テスト-ünïcødé",  # tous les alphabets sont acceptés
        "a" + "b9Xq" * 31,  # 125 caractères : sous la limite
    ],
)
def test_passwords_accepted(good):
    password_rules.check(good, ("rob", "Rob"), fetch=_without_leak)


@pytest.mark.parametrize(
    "low, reason",
    [
        ("", "15 caractères"),
        ("Court-mais-ok", "15 caractères"),  # 13 caractères
        ("x" * 129, "dépasser"),
        ("ligne un\nligne deux xx", "retour à la ligne"),
        ("aaaaaaaaaaaaaaaa", "répétitif"),
        ("abcabcabcabcabcabc", "répétitif"),
        ("121212121212121212", "répétitif"),
        ("1234567890123456", "suite"),
        ("abcdefghijklmnop", "suite"),
        ("azertyuiopqsdfgh", "suite"),
        ("ponmlkjihgfedcba", "suite"),  # à l'envers
        ("1q2w3e4r5t6y7u8i", "suite"),
        ("Motdepasse2026!!", "courant"),
        ("MOT-DE-PASSE-2026", "courant"),
        ("Password123456789", "courant"),
        ("jetaimejetaime2026", "courant"),
        ("Soleil-Azerty-1985", "courant"),
        ("Rob-Rob-2026-04-12", "courant"),  # le nom de la personne
        ("Jabba-2026-Admin!!", "courant"),
    ],
)
def test_passwords_refused(low, reason):
    with pytest.raises(ValueError, match=reason):
        password_rules.check(low, ("rob", "Rob"), fetch=_without_leak)


def _service_answer(mot_de_passe, number):
    fingerprint = hashlib.sha1(mot_de_passe.encode()).hexdigest().upper()
    return f"0000000000000000000000000000000000A:0\r\n{fingerprint[5:]}:{number}\r\n"


def test_known_leak_refused(monkeypatch):
    monkeypatch.setenv("JABBA_VERIF_FUITES", "1")
    word = "une phrase qui a fuité un jour"
    sent_ones = []

    def service(prefix):
        sent_ones.append(prefix)
        return _service_answer(word, 42)

    with pytest.raises(ValueError, match="fuites"):
        password_rules.check(word, fetch=service)
    # seuls les 5 premiers caractères de l'empreinte partent, jamais le mot de passe
    assert sent_ones == [hashlib.sha1(word.encode()).hexdigest().upper()[:5]]


def test_leak_ignored_if_zero_count_or_other_suffix(monkeypatch):
    monkeypatch.setenv("JABBA_VERIF_FUITES", "1")
    word = "une phrase jamais vue ailleurs"
    password_rules.check(word, fetch=lambda prefix: _service_answer(word, 0))  # remplissage du service
    password_rules.check(word, fetch=lambda prefix: "ABCDEF:12\r\n")


def test_without_internet_nothing_blocks(monkeypatch):
    monkeypatch.setenv("JABBA_VERIF_FUITES", "1")

    def broken(prefix):
        raise OSError("pas de réseau")

    assert password_rules.breached("une phrase quelconque ici", broken) is False


def test_check_can_be_disabled(monkeypatch):
    monkeypatch.setenv("JABBA_VERIF_FUITES", "0")
    assert password_rules.breached("x", lambda prefix: pytest.fail("aucun appel attendu")) is False


def test_accounts_apply_the_rules():
    with pytest.raises(accounts.Refusal) as refusals:
        accounts.check_password_strength("Motdepasse2026!!")
    assert refusals.value.code == 400


def test_the_server_password_applies_the_rules(monkeypatch):
    calls = []
    monkeypatch.setattr(commands, "run_action", lambda *a, **k: calls.append(a) or (0, "", ""))
    with pytest.raises(ValueError, match="15 caractères"):
        server_password.change_password("rob", "court")
    with pytest.raises(ValueError, match="«"):
        server_password.change_password("rob", "deux points : interdits ici")
    assert calls == []  # rien n'est envoyé au serveur
