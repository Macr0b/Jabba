"""Double vérification des admins : après le mot de passe, une deuxième preuve.

Chaque admin choisit sa ou ses méthodes :
- « cle »    : clé d'accès (Face ID, Touch ID, clé USB) ;
- « appli »  : appli d'authentification (code à 6 chiffres) ;
- « secours »: 10 codes de secours à usage unique, créés avec la première méthode.

Sommaire
- état         : methods, summary, clear
- appli        : start_app_enrolment, finish_app_enrolment, remove_app
- clés         : start_key_enrolment, finish_key_enrolment, remove_key
- secours      : new_recovery_codes
- connexion    : start_login_step, key_login_options, finish_login_step

Seules les empreintes des codes de secours et les clés publiques sont gardées.
Le secret de l'appli d'authentification est forcément gardé tel quel (c'est le
principe du TOTP) : le fichier est lisible par l'appli seule (0600).
  SecondFactorError : le refus d'un code, avec son code HTTP et sa phrase
  interne : _read, _write, _entry, _has_method, _code_hash, _new_code, _store_new_codes, _use_recovery_code, _first_method_codes, _drop_expired_steps, _check_key, _check_app
"""

import hashlib
import hmac
import os
import secrets
import threading
import time

from security import passkeys, totp
from services import json_files

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
DATA_FILE = os.path.join(DATA_DIR, "double-verification.json")
RECOVERY_CODE_COUNT = 10
ENROLMENT_LIFETIME = 10 * 60
LOGIN_STEP_LIFETIME = 5 * 60
LOGIN_STEP_ATTEMPTS = 5
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # sans 0/o, 1/l/i : se recopie sans erreur
_lock = threading.RLock()
_app_enrolments = {}  # identifiant -> {"secret", "exp"}
_key_enrolments = {}  # identifiant -> {"defi", "exp"}
_login_steps = {}  # jeton -> {"compte", "exp", "essais", "defi"}


class SecondFactorError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------- fichier
def _read():
    return json_files.load(DATA_FILE)


def _write(data):
    json_files.save(DATA_FILE, data, private=True)


def _entry(data, identifier):
    entry = data.setdefault(identifier, {})
    entry.setdefault("keys", [])
    entry.setdefault("codes", [])
    entry.setdefault("handle", secrets.token_hex(16))  # identifiant anonyme donné aux clés d'accès
    return entry


def _has_method(entry):
    return bool(entry.get("app") or entry.get("keys"))


# ---------------------------------------------------------------- état
def methods(identifier):
    """Les méthodes actives du compte, dans l'ordre où on les propose."""
    entry = _read().get(identifier) or {}
    found = []
    if entry.get("keys"):
        found.append("cle")
    if entry.get("app"):
        found.append("appli")
    if found and entry.get("codes"):
        found.append("secours")
    return found


def summary(identifier):
    entry = _read().get(identifier) or {}
    return {
        "active": _has_method(entry),
        "app": bool(entry.get("app")),
        "keys": [
            {
                "id": key["id"],
                "name": key.get("name", "Clé"),
                "created": key.get("created"),
                "used_space": key.get("used_space"),
            }
            for key in entry.get("keys", [])
        ],
        "codes_left": len(entry.get("codes", [])),
    }


def clear(identifier):
    """Retire toute la double vérification d'un compte (téléphone perdu, par un super admin)."""
    with _lock:
        data = _read()
        if data.pop(identifier, None) is not None:
            _write(data)


# ---------------------------------------------------------------- codes de secours
def _code_hash(code, salt):
    normalized = "".join(character for character in code.lower() if character.isalnum())
    return hashlib.scrypt(normalized.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()


def _new_code():
    letters = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return letters[:4] + "-" + letters[4:]


def _store_new_codes(entry):
    codes = [_new_code() for _ in range(RECOVERY_CODE_COUNT)]
    entry["codes"] = []
    for code in codes:
        salt = secrets.token_hex(16)
        entry["codes"].append({"salt": salt, "fingerprint": _code_hash(code, salt)})
    return codes


def new_recovery_codes(identifier):
    with _lock:
        data = _read()
        entry = _entry(data, identifier)
        if not _has_method(entry):
            raise SecondFactorError(400, "Active d'abord Face ID ou une appli d'authentification.")
        codes = _store_new_codes(entry)
        _write(data)
        return codes


def _use_recovery_code(entry, typed):
    for stored in entry.get("codes", []):
        if hmac.compare_digest(_code_hash(typed or "", stored["salt"]), stored["fingerprint"]):
            entry["codes"].remove(stored)
            return True
    return False


def _first_method_codes(entry):
    """Avec la première méthode, on crée les codes de secours (montrés une seule fois)."""
    if entry.get("codes"):
        return None
    return _store_new_codes(entry)


# ---------------------------------------------------------------- appli d'authentification
def start_app_enrolment(identifier):
    """(secret, lien otpauth) à montrer en QR code ; valable 10 minutes."""
    secret = totp.new_secret()
    with _lock:
        _app_enrolments[identifier] = {"secret": secret, "exp": time.time() + ENROLMENT_LIFETIME}
    return secret, totp.provisioning_uri(secret, identifier)


def finish_app_enrolment(identifier, code, moment=None):
    """Active l'appli si le code tapé est bon. Renvoie les codes de secours s'ils viennent d'être créés."""
    moment = time.time() if moment is None else moment
    with _lock:
        pending = _app_enrolments.get(identifier)
        if not pending or pending["exp"] < moment:
            raise SecondFactorError(400, "Le QR code a expiré : recommence.")
        step = totp.matching_step(pending["secret"], code, moment)
        if step is None:
            raise SecondFactorError(400, "Code incorrect. Vérifie que l'heure du téléphone est à jour.")
        data = _read()
        entry = _entry(data, identifier)
        entry["app"] = {"secret": pending["secret"], "last_slot": step, "created": int(moment)}
        codes = _first_method_codes(entry)
        _write(data)
        del _app_enrolments[identifier]
        return codes


def remove_app(identifier):
    with _lock:
        data = _read()
        entry = _entry(data, identifier)
        entry.pop("app", None)
        if not _has_method(entry):
            entry["codes"] = []
        _write(data)


# ---------------------------------------------------------------- clés d'accès
def start_key_enrolment(identifier, display_name, rp_id):
    with _lock:
        data = _read()
        entry = _entry(data, identifier)
        _write(data)  # garde la « poignée » créée au premier passage
        options, challenge = passkeys.registration_options(
            rp_id, entry["handle"], identifier, display_name, [key["id"] for key in entry["keys"]]
        )
        _key_enrolments[identifier] = {"challenge": challenge, "exp": time.time() + ENROLMENT_LIFETIME}
        return options


def finish_key_enrolment(identifier, credential, key_name, rp_id, origins):
    with _lock:
        pending = _key_enrolments.pop(identifier, None)
        if not pending or pending["exp"] < time.time():
            raise SecondFactorError(400, "La demande a expiré : recommence.")
        try:
            key = passkeys.verify_registration(credential, pending["challenge"], rp_id, origins)
        except Exception:
            raise SecondFactorError(400, "La clé n'a pas pu être vérifiée. Réessaie.")
        data = _read()
        entry = _entry(data, identifier)
        key.update({"name": (key_name or "").strip()[:60] or "Clé d'accès", "created": int(time.time())})
        entry["keys"].append(key)
        codes = _first_method_codes(entry)
        _write(data)
        return codes


def remove_key(identifier, key_id):
    with _lock:
        data = _read()
        entry = _entry(data, identifier)
        remaining = [key for key in entry["keys"] if key["id"] != key_id]
        if len(remaining) == len(entry["keys"]):
            raise SecondFactorError(404, "Clé introuvable.")
        entry["keys"] = remaining
        if not _has_method(entry):
            entry["codes"] = []
        _write(data)


# ---------------------------------------------------------------- connexion en deux étapes
def _drop_expired_steps():
    now = time.time()
    for token in [token for token, step in _login_steps.items() if step["exp"] < now]:
        del _login_steps[token]


def start_login_step(account):
    """Le mot de passe est bon : on garde le compte de côté le temps de la 2e preuve."""
    token = secrets.token_urlsafe(24)
    with _lock:
        _drop_expired_steps()
        _login_steps[token] = {
            "account": dict(account),
            "exp": time.time() + LOGIN_STEP_LIFETIME,
            "attempts": 0,
            "challenge": None,
        }
    return token


def _step(token):
    _drop_expired_steps()
    step = _login_steps.get(token or "")
    if not step:
        raise SecondFactorError(401, "Étape expirée : retape ton mot de passe.")
    return step


def key_login_options(token, rp_id):
    with _lock:
        step = _step(token)
        entry = _read().get(step["account"]["username"]) or {}
        credential_ids = [key["id"] for key in entry.get("keys", [])]
        if not credential_ids:
            raise SecondFactorError(400, "Aucune clé d'accès sur ce compte.")
        options, challenge = passkeys.authentication_options(rp_id, credential_ids)
        step["challenge"] = challenge
        return options


def _check_key(entry, step, credential, rp_id, origins):
    if not step["challenge"] or not isinstance(credential, dict):
        return False
    stored = next((key for key in entry.get("keys", []) if key["id"] == credential.get("id")), None)
    if not stored:
        return False
    try:
        stored["counter"] = passkeys.verify_authentication(credential, step["challenge"], rp_id, origins, stored)
    except Exception:
        return False
    stored["used_space"] = int(time.time())
    return True


def _check_app(entry, code, moment):
    app = entry.get("app")
    if not app:
        return False
    step_number = totp.matching_step(app["secret"], code, moment, app.get("last_slot", -1))
    if step_number is None:
        return False
    app["last_slot"] = step_number
    return True


def finish_login_step(token, method, answer, rp_id="", origins=(), moment=None):
    """Le compte si la 2e preuve est bonne ; None si elle est fausse (l'erreur compte)."""
    moment = time.time() if moment is None else moment
    with _lock:
        step = _step(token)
        identifier = step["account"]["username"]
        data = _read()
        entry = _entry(data, identifier)
        if method == "cle":
            accepted = _check_key(entry, step, answer, rp_id, list(origins))
        elif method == "appli":
            accepted = _check_app(entry, answer, moment)
        elif method == "secours":
            accepted = _use_recovery_code(entry, answer if isinstance(answer, str) else "")
        else:
            raise SecondFactorError(400, "Méthode inconnue.")
        if not accepted:
            step["attempts"] += 1
            step["challenge"] = None
            if step["attempts"] >= LOGIN_STEP_ATTEMPTS:
                del _login_steps[token]
            return None
        _write(data)
        del _login_steps[token]
        return step["account"]
