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


def _token_hash(jeton):
    return hashlib.sha256(jeton.encode()).hexdigest()


def _read():
    if _cache["d"] is None:
        try:
            with open(SESSIONS_FILE) as f:
                _cache["d"] = json.load(f)
        except (OSError, ValueError):
            _cache["d"] = {}
    return _cache["d"]


def _write():
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SESSIONS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_cache["d"], f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SESSIONS_FILE)
    _cache["ecrit"] = time.time()


def _drop_expired(d):
    maintenant = time.time()
    for k in [k for k, s in d.items() if s["exp"] < maintenant]:
        del d[k]


def create(identifiant, rang, provisoire=False, appareil=None, login=None):
    jeton = secrets.token_urlsafe(32)
    maintenant = time.time()
    with _lock:
        d = _read()
        _drop_expired(d)
        d[_token_hash(jeton)] = {"id": secrets.token_hex(4), "identifiant": identifiant, "rang": rang, "provisoire": provisoire,
                        "appareil": appareil, "login": login, "cree": int(maintenant), "vu": int(maintenant),
                        "exp": maintenant + (TEMPORARY_LIFETIME if provisoire else LIFETIME), "confirme": maintenant}
        _write()
    return jeton


def read(jeton):
    """La session (copie) si le jeton est valable, sinon None."""
    if not jeton:
        return None
    with _lock:
        s = _read().get(_token_hash(jeton))
        if not s or s["exp"] < time.time():
            return None
        if time.time() - s["vu"] > 300:
            s["vu"] = int(time.time())
            _write()
        return dict(s)


def confirm(jeton):
    with _lock:
        s = _read().get(_token_hash(jeton))
        if s:
            s["confirme"] = time.time()
            _write()


def confirmed_recently(session):
    return time.time() - session.get("confirme", 0) < CONFIRMATION_WINDOW


def close(jeton):
    with _lock:
        if _read().pop(_token_hash(jeton or ""), None):
            _write()


def close_by_id(sid):
    with _lock:
        d = _read()
        for k in [k for k, s in d.items() if s["id"] == sid]:
            del d[k]
            _write()
            return True
    return False


def close_account(identifiant=None):
    """Ferme toutes les sessions d'un compte (ou de tout le monde)."""
    with _lock:
        d = _read()
        for k in [k for k, s in d.items() if identifiant is None or s["identifiant"] == identifiant]:
            del d[k]
        _write()


def list_sessions():
    with _lock:
        d = _read()
        _drop_expired(d)
        return sorted(({k: v for k, v in s.items() if k != "exp"} for s in d.values() if not s["provisoire"]),
                      key=lambda s: -s["vu"])
