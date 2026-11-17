"""Règles des nouveaux mots de passe (NIST SP 800-63B-4) : longueur, mots courants, suites, fuites."""

import hashlib

import pytest

from host import commands
from security import accounts, password_rules, server_password


def _sans_fuite(prefixe):
    return ""


@pytest.mark.parametrize(
    "bon",
    [
        "abeille camion soleil 48",  # une phrase de mots, même avec un mot courant dedans
        "La lune danse sur le toit",
        "Café-crème-à-Strasbourg",
        "x" * 3 + "Kp9!vRt2#qLm8sW",  # une petite répétition au début ne gêne pas
        "é" * 2 + "ñ€漢字テスト-ünïcødé",  # tous les alphabets sont acceptés
        "a" + "b9Xq" * 31,  # 125 caractères : sous la limite
    ],
)
def test_mots_de_passe_acceptes(bon):
    password_rules.check(bon, ("rob", "Rob"), fetch=_sans_fuite)


@pytest.mark.parametrize(
    "faible, raison",
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
def test_mots_de_passe_refuses(faible, raison):
    with pytest.raises(ValueError, match=raison):
        password_rules.check(faible, ("rob", "Rob"), fetch=_sans_fuite)


def _reponse_du_service(mot_de_passe, nombre):
    empreinte = hashlib.sha1(mot_de_passe.encode()).hexdigest().upper()
    return f"0000000000000000000000000000000000A:0\r\n{empreinte[5:]}:{nombre}\r\n"


def test_fuite_connue_refusee(monkeypatch):
    monkeypatch.setenv("JABBA_VERIF_FUITES", "1")
    mot = "une phrase qui a fuité un jour"
    envoyes = []

    def service(prefixe):
        envoyes.append(prefixe)
        return _reponse_du_service(mot, 42)

    with pytest.raises(ValueError, match="fuites"):
        password_rules.check(mot, fetch=service)
    # seuls les 5 premiers caractères de l'empreinte partent, jamais le mot de passe
    assert envoyes == [hashlib.sha1(mot.encode()).hexdigest().upper()[:5]]


def test_fuite_ignoree_si_compte_nul_ou_autre_suffixe(monkeypatch):
    monkeypatch.setenv("JABBA_VERIF_FUITES", "1")
    mot = "une phrase jamais vue ailleurs"
    password_rules.check(mot, fetch=lambda prefixe: _reponse_du_service(mot, 0))  # remplissage du service
    password_rules.check(mot, fetch=lambda prefixe: "ABCDEF:12\r\n")


def test_sans_internet_on_ne_bloque_pas(monkeypatch):
    monkeypatch.setenv("JABBA_VERIF_FUITES", "1")

    def en_panne(prefixe):
        raise OSError("pas de réseau")

    assert password_rules.breached("une phrase quelconque ici", en_panne) is False


def test_verification_desactivable(monkeypatch):
    monkeypatch.setenv("JABBA_VERIF_FUITES", "0")
    assert password_rules.breached("x", lambda prefixe: pytest.fail("aucun appel attendu")) is False


def test_les_comptes_appliquent_les_regles():
    with pytest.raises(accounts.Refusal) as refus:
        accounts.check_password_strength("Motdepasse2026!!")
    assert refus.value.code == 400


def test_le_mot_de_passe_du_serveur_applique_les_regles(monkeypatch):
    appels = []
    monkeypatch.setattr(commands, "run_action", lambda *a, **k: appels.append(a) or (0, "", ""))
    with pytest.raises(ValueError, match="15 caractères"):
        server_password.change_password("rob", "court")
    with pytest.raises(ValueError, match="«"):
        server_password.change_password("rob", "deux points : interdits ici")
    assert appels == []  # rien n'est envoyé au serveur
