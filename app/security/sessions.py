"""Connexions admin mémorisées sur chaque appareil (30 jours).

Le jeton vit dans un cookie protégé (HttpOnly, Secure, SameSite=Strict). Le
serveur n'en garde que l'empreinte. Une session peut être fermée à distance.
Les actions les plus sensibles redemandent le mot de passe si sa dernière
saisie date de plus de 10 minutes (« confirmation »).
"""

import hashlib
import json
import os
import secrets
import threading
import time

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
LIFETIME = 30 * 86400
TEMPORARY_LIFETIME = 15 * 60
CONFIRMATION_WINDOW = 10 * 60
COOKIE = "jabba_session"
_lock = threading.RLock()
_cache = {"d": None, "ecrit": 0}


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _read():
    if _cache["d"] is None:
        try:
            with open(SESSIONS_FILE) as file:
                _cache["d"] = json.load(file)
        except (OSError, ValueError):
            _cache["d"] = {}
    return _cache["d"]


def _write():
    os.makedirs(DATA_DIR, exist_ok=True)
    temporary_file = SESSIONS_FILE + ".tmp"
    with open(temporary_file, "w") as file:
        json.dump(_cache["d"], file)
    os.chmod(temporary_file, 0o600)
    os.replace(temporary_file, SESSIONS_FILE)
    _cache["ecrit"] = time.time()


def _drop_expired(sessions_data):
    now = time.time()
    for key in [key for key, session in sessions_data.items() if session["exp"] < now]:
        del sessions_data[key]


def create(identifier, rank, temporary=False, device=None, login=None):
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        sessions_data = _read()
        _drop_expired(sessions_data)
        sessions_data[_token_hash(token)] = {
            "id": secrets.token_hex(4),
            "identifiant": identifier,
            "rang": rank,
            "provisoire": temporary,
            "appareil": device,
            "login": login,
            "cree": int(now),
            "vu": int(now),
            "exp": now + (TEMPORARY_LIFETIME if temporary else LIFETIME),
            "confirme": now,
        }
        _write()
    return token


def read(token):
    """La session (copie) si le jeton est valable, sinon None."""
    if not token:
        return None
    with _lock:
        session = _read().get(_token_hash(token))
        if not session or session["exp"] < time.time():
            return None
        if time.time() - session["vu"] > 300:
            session["vu"] = int(time.time())
            _write()
        return dict(session)


def confirm(token):
    with _lock:
        session = _read().get(_token_hash(token))
        if session:
            session["confirme"] = time.time()
            _write()


def confirmed_recently(session):
    return time.time() - session.get("confirme", 0) < CONFIRMATION_WINDOW


def close(token):
    with _lock:
        if _read().pop(_token_hash(token or ""), None):
            _write()


def close_by_id(session_id):
    with _lock:
        sessions_data = _read()
        for key in [key for key, session in sessions_data.items() if session["id"] == session_id]:
            del sessions_data[key]
            _write()
            return True
    return False


def close_account(identifier=None):
    """Ferme toutes les sessions d'un compte (ou de tout le monde)."""
    with _lock:
        sessions_data = _read()
        for key in [
            key for key, session in sessions_data.items() if identifier is None or session["identifiant"] == identifier
        ]:
            del sessions_data[key]
        _write()


def list_sessions():
    with _lock:
        sessions_data = _read()
        _drop_expired(sessions_data)
        return sorted(
            (
                {key: value for key, value in session.items() if key != "exp"}
                for session in sessions_data.values()
                if not session["provisoire"]
            ),
            key=lambda session: -session["vu"],
        )
