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

DOSSIER = os.environ.get("JABBA_DATA", "/data")
FICHIER = os.path.join(DOSSIER, "sessions.json")
DUREE = 30 * 86400
DUREE_PROVISOIRE = 15 * 60
CONFIRMATION = 10 * 60
COOKIE = "jabba_session"
_verrou = threading.RLock()
_cache = {"d": None, "ecrit": 0}


def _h(jeton):
    return hashlib.sha256(jeton.encode()).hexdigest()


def _lire():
    if _cache["d"] is None:
        try:
            with open(FICHIER) as f:
                _cache["d"] = json.load(f)
        except (OSError, ValueError):
            _cache["d"] = {}
    return _cache["d"]


def _ecrire():
    os.makedirs(DOSSIER, exist_ok=True)
    tmp = FICHIER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_cache["d"], f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, FICHIER)
    _cache["ecrit"] = time.time()


def _nettoyer(d):
    maintenant = time.time()
    for k in [k for k, s in d.items() if s["exp"] < maintenant]:
        del d[k]


def creer(identifiant, rang, provisoire=False, appareil=None, login=None):
    jeton = secrets.token_urlsafe(32)
    maintenant = time.time()
    with _verrou:
        d = _lire()
        _nettoyer(d)
        d[_h(jeton)] = {"id": secrets.token_hex(4), "identifiant": identifiant, "rang": rang, "provisoire": provisoire,
                        "appareil": appareil, "login": login, "cree": int(maintenant), "vu": int(maintenant),
                        "exp": maintenant + (DUREE_PROVISOIRE if provisoire else DUREE), "confirme": maintenant}
        _ecrire()
    return jeton


def lire(jeton):
    """La session (copie) si le jeton est valable, sinon None."""
    if not jeton:
        return None
    with _verrou:
        s = _lire().get(_h(jeton))
        if not s or s["exp"] < time.time():
            return None
        if time.time() - s["vu"] > 300:
            s["vu"] = int(time.time())
            _ecrire()
        return dict(s)


def confirmer(jeton):
    with _verrou:
        s = _lire().get(_h(jeton))
        if s:
            s["confirme"] = time.time()
            _ecrire()


def confirme_recemment(session):
    return time.time() - session.get("confirme", 0) < CONFIRMATION


def fermer(jeton):
    with _verrou:
        if _lire().pop(_h(jeton or ""), None):
            _ecrire()


def fermer_id(sid):
    with _verrou:
        d = _lire()
        for k in [k for k, s in d.items() if s["id"] == sid]:
            del d[k]
            _ecrire()
            return True
    return False


def fermer_compte(identifiant=None):
    """Ferme toutes les sessions d'un compte (ou de tout le monde)."""
    with _verrou:
        d = _lire()
        for k in [k for k, s in d.items() if identifiant is None or s["identifiant"] == identifiant]:
            del d[k]
        _ecrire()


def lister():
    with _verrou:
        d = _lire()
        _nettoyer(d)
        return sorted(({k: v for k, v in s.items() if k != "exp"} for s in d.values() if not s["provisoire"]),
                      key=lambda s: -s["vu"])
