"""Règles des nouveaux mots de passe, d'après le NIST SP 800-63B-4 (2025).

Sommaire
- check(password, context_words) : refuse un mot de passe trop faible (ValueError, message en clair).
- breached(password, fetch) : vrai si le mot de passe figure dans une fuite connue.

Ce qu'on demande :
- au moins 15 caractères (le NIST les exige quand le mot de passe est le seul facteur) ;
- aucune règle de composition (pas d'obligation de majuscule, de chiffre ou de symbole) ;
- pas un mot de passe courant, ni une suite de touches (azerty…, 123456…), ni une répétition ;
- pas construit sur le nom de la personne, son identifiant ou le nom de l'appli ;
- pas présent dans une fuite connue (service « Pwned Passwords » : seuls les 5 premiers
  caractères de l'empreinte SHA-1 partent sur Internet, jamais le mot de passe).

Les mots de passe déjà enregistrés continuent de marcher : seules les créations
et les changements passent par ces règles.
"""

import hashlib
import os
import unicodedata
import urllib.request

MIN_LENGTH = 15
MAX_LENGTH = 128
MIN_DISTINCT_CHARACTERS = 5
MIN_REMAINING_LETTERS = 4
BREACH_SERVICE = "https://api.pwnedpasswords.com/range/"
BREACH_TIMEOUT_SECONDS = 3

# Suites de touches et d'alphabet, lues dans les deux sens.
SEQUENCES = (
    "0123456789" * 4,
    "abcdefghijklmnopqrstuvwxyz",
    "azertyuiopqsdfghjklmwxcvbn",
    "qwertyuiopasdfghjklzxcvbnm",
    "qwertzuiopasdfghjklyxcvbnm",
    "1a2z3e4r5t6y7u8i9o0p",
    "1q2w3e4r5t6y7u8i9o0p",
    "a1z2e3r4t5y6u7i8o9p0",
)

# Mots qui reviennent dans les mots de passe les plus courants (listes publiques de fuites).
COMMON_WORDS = (
    "password",
    "motdepasse",
    "passw0rd",
    "azerty",
    "qwerty",
    "admin",
    "administrateur",
    "administrator",
    "root",
    "user",
    "utilisateur",
    "welcome",
    "bienvenue",
    "bonjour",
    "salut",
    "soleil",
    "jetaime",
    "iloveyou",
    "loulou",
    "doudou",
    "chouchou",
    "chocolat",
    "princesse",
    "dragon",
    "monkey",
    "football",
    "marseille",
    "paris",
    "france",
    "secret",
    "letmein",
    "changeme",
    "default",
    "nextcloud",
    "jellyfin",
    "tailscale",
    "jabba",
    "serveur",
    "server",
)


def _letters_only(text):
    """Minuscules sans accents, lettres seulement : « Mot-de-Passe 2026 ! » → « motdepasse »."""
    without_accents = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(character for character in without_accents.lower() if character.isalpha())


def _is_repetition(password):
    """Vrai pour « aaaa… », « abcabcabc… » ou trop peu de caractères différents."""
    if len(set(password)) < MIN_DISTINCT_CHARACTERS:
        return True
    for pattern_length in range(1, len(password) // 3 + 1):
        pattern = password[:pattern_length]
        repeated = pattern * (len(password) // pattern_length + 1)
        if repeated[: len(password)] == password:
            return True
    return False


def _is_sequence(password):
    lowered = password.lower()
    for sequence in SEQUENCES:
        doubled = sequence * 2
        if lowered in doubled or lowered in doubled[::-1]:
            return True
    return False


def _is_built_on_words(password, words):
    """Vrai si, une fois retirés les mots interdits, il reste moins de 4 lettres."""
    remaining = _letters_only(password)
    for word in sorted(words, key=len, reverse=True):
        if len(word) >= 3:
            remaining = remaining.replace(word, "")
    return len(remaining) < MIN_REMAINING_LETTERS


def _fetch_breach_range(prefix):
    request = urllib.request.Request(BREACH_SERVICE + prefix, headers={"Add-Padding": "true"})
    with urllib.request.urlopen(request, timeout=BREACH_TIMEOUT_SECONDS) as response:
        return response.read().decode()


def breached(password, fetch=_fetch_breach_range):
    """Vrai si le mot de passe est dans une fuite connue. Sans Internet, on ne bloque pas."""
    if os.environ.get("JABBA_VERIF_FUITES", "1") == "0":
        return False
    digest = hashlib.sha1(password.encode()).hexdigest().upper()
    prefix = digest[:5]
    suffix = digest[5:]
    try:
        answer = fetch(prefix)
    except Exception:
        return False
    for line in answer.splitlines():
        found_suffix, _, count = line.strip().partition(":")
        if found_suffix == suffix and count.strip() not in ("", "0"):
            return True
    return False


def check(password, context_words=(), fetch=_fetch_breach_range):
    """Lève ValueError avec un message à afficher si le mot de passe ne convient pas."""
    password = password or ""
    if len(password) < MIN_LENGTH:
        raise ValueError(f"Le mot de passe doit faire au moins {MIN_LENGTH} caractères.")
    if len(password) > MAX_LENGTH:
        raise ValueError(f"Le mot de passe ne peut pas dépasser {MAX_LENGTH} caractères.")
    if any(character in password for character in "\n\r"):
        raise ValueError("Le mot de passe ne peut pas contenir de retour à la ligne.")
    if _is_repetition(password):
        raise ValueError("Ce mot de passe est trop répétitif. Une phrase de quelques mots convient très bien.")
    if _is_sequence(password):
        raise ValueError("Une suite de touches ou de chiffres se devine trop vite.")
    personal_words = [_letters_only(word) for word in context_words if word]
    if _is_built_on_words(password, COMMON_WORDS + tuple(personal_words)):
        raise ValueError(
            "Ce mot de passe repose sur un mot très courant ou sur ton nom. Une phrase de quelques mots convient très bien."
        )
    if breached(password, fetch):
        raise ValueError("Ce mot de passe apparaît dans des fuites de données connues : choisis-en un autre.")
