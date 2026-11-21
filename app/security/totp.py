"""Codes à 6 chiffres des applis d'authentification (TOTP, RFC 6238), sans bibliothèque externe.

Sommaire
- new_secret() : un secret aléatoire, en base32 (ce que lit l'appli).
- code_at(secret, moment) : le code valable à ce moment.
- matching_step(secret, code, moment, last_step) : le créneau reconnu, ou None.
- provisioning_uri(secret, account_name) : le lien « otpauth:// » mis dans le QR code.

Un code change toutes les 30 secondes. On accepte le créneau précédent et le
suivant (horloge du téléphone un peu décalée), mais jamais deux fois le même
créneau : un code vu par-dessus l'épaule ne resservira pas.
  interne : _secret_bytes, _code_for_step
"""

import base64
import hashlib
import hmac
import secrets
import struct
import urllib.parse

STEP_SECONDS = 30
DIGITS = 6
ACCEPTED_DRIFT_STEPS = 1
ISSUER = "Jabba"


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _secret_bytes(secret):
    cleaned = secret.replace(" ", "").upper()
    padding = "=" * (-len(cleaned) % 8)
    return base64.b32decode(cleaned + padding)


def _code_for_step(secret, step):
    digest = hmac.new(_secret_bytes(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def code_at(secret, moment):
    return _code_for_step(secret, int(moment // STEP_SECONDS))


def matching_step(secret, code, moment, last_step=-1):
    """Le numéro de créneau si le code est bon (et pas déjà utilisé), sinon None."""
    typed = "".join(character for character in str(code or "") if character.isdigit())
    if len(typed) != DIGITS:
        return None
    current = int(moment // STEP_SECONDS)
    for step in range(current - ACCEPTED_DRIFT_STEPS, current + ACCEPTED_DRIFT_STEPS + 1):
        if step <= last_step:
            continue
        if hmac.compare_digest(_code_for_step(secret, step), typed):
            return step
    return None


def provisioning_uri(secret, account_name):
    label = urllib.parse.quote(f"{ISSUER}:{account_name}")
    query = urllib.parse.urlencode({"secret": secret, "issuer": ISSUER, "digits": DIGITS, "period": STEP_SECONDS})
    return f"otpauth://totp/{label}?{query}"
