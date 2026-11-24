"""Comptes de l'appli : super admin, admins, et qui se connecte.

- Le super admin principal, c'est le compte du serveur (mot de passe du serveur).
  Il existe toujours : on ne peut donc jamais se retrouver sans super admin.
- Les autres comptes (admin ou super admin) ont un mot de passe propre à
  l'appli, gardé seulement sous forme d'empreinte (scrypt).
- Un compte peut être relié à un compte Tailscale : cette personne peut alors
  ouvrir l'appli, et ses appareils reçoivent ce qui la concerne.
- Mot de passe oublié : la demande part chez les super admins ; une fois
  acceptée, un mot de passe provisoire (24 h, à changer) s'affiche une seule
  fois sur l'appareil de la personne qui a fait la demande. Il n'est jamais écrit
  sur le disque.

Sommaire :
  Refusal                  : le refus d'un geste sur les comptes, avec son code et sa phrase
  check_password_strength  : applique les règles de security.password_rules (15 caractères, pas de mot courant…)
  temporary_password       : mot de passe provisoire lisible : 3 mots et 2 chiffres
  list_accounts            : les comptes admin, les réglages et les demandes en attente
  find                     : le compte de cet identifiant, ou None
  allowed_logins           : comptes Tailscale reliés à un compte de l'appli : ils peuvent ouvrir l'appli
  super_admin_logins       : les comptes Tailscale des super admins
  admin_space_open         : vrai si l'espace admins est ouvert
  check_credentials        : le compte (dict public) si le mot de passe est bon, sinon None
  create                   : crée un compte admin avec un mot de passe provisoire (rang vérifié)
  update                   : change le rang, le nom ou le compte Tailscale d'un admin
  delete                   : retire un compte admin
  change_own_password      : change son propre mot de passe (règles vérifiées)
  set_settings             : ouvre ou ferme l'espace admins
  request_reset            : enregistre la demande. Renvoie le compte concerné, ou None (sans le dire à l'appelant)
  answer_reset             : accepte ou refuse une demande de mot de passe oublié
  pending_reset            : vrai si ce compte Tailscale a une remise à zéro acceptée et encore valable
  collect_reset            : le mot de passe provisoire, une seule fois, pour la personne qui l'attend
  record_visit             : note une visite (nom, appareil, première et dernière fois)
  record_refusal           : garde les 50 dernières tentatives refusées. True si c'est la première depuis 1 h pour ce compte
  recent_logins            : les dernières visites et les derniers refus
  interne : _read, _write, _password_hash, _public_view, _check_identifier, _check_login, _account
"""

import hashlib
import hmac
import os
import re
import secrets
import threading
import time

from security import password_rules
from services import json_files

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "comptes.json")
ROLES = ("admin", "super")
RESET_LIFETIME = 24 * 3600
_lock = threading.RLock()
_pending_resets = {}  # login Tailscale -> {"identifiant", "mot_de_passe", "exp"}
_visits = {}  # login -> {"nom", "appareil", "derniere", "premiere", "n"} (copie en mémoire)
_last_flush = [0]


class Refusal(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _read():
    data = json_files.load(ACCOUNTS_FILE)
    data.setdefault("accounts", [])
    data.setdefault("settings", {"admin_space": True})
    data.setdefault("requests", [])
    data.setdefault("visits", {})
    data.setdefault("refusals", [])
    return data


def _write(data):
    json_files.save(ACCOUNTS_FILE, data, private=True)


def _password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()


def _public_view(account):
    result = {
        key: account.get(key)
        for key in (
            "id",
            "username",
            "name",
            "rank",
            "tailscale",
            "temporary",
            "created",
            "created_by",
            "last_login",
        )
    }
    return result


def check_password_strength(password, context_words=()):
    """Applique les règles de security.password_rules (15 caractères, pas de mot courant…)."""
    try:
        password_rules.check(password, context_words)
    except ValueError as error:
        raise Refusal(400, str(error))


def temporary_password():
    """Mot de passe provisoire lisible : 3 mots et 2 chiffres."""
    from security.server_password import WORDS

    return "-".join(secrets.choice(WORDS) for _ in range(3)) + "-" + str(secrets.randbelow(90) + 10)


# ---------------------------------------------------------------- lecture
def list_accounts():
    with _lock:
        data = _read()
        return {
            "accounts": [_public_view(account) for account in data["accounts"]],
            "settings": data["settings"],
            "requests": [entry for entry in data["requests"] if entry["state"] == "attente"],
        }


def find(identifier_value):
    identifier = (identifier_value or "").strip().lower()
    for account in _read()["accounts"]:
        if account["username"] == identifier:
            return account
    return None


def allowed_logins():
    """Comptes Tailscale reliés à un compte de l'appli : ils peuvent ouvrir l'appli."""
    return {account["tailscale"] for account in _read()["accounts"] if account.get("tailscale")}


def super_admin_logins():
    return {
        account["tailscale"]
        for account in _read()["accounts"]
        if account.get("tailscale") and account["rank"] == "super"
    }


def admin_space_open():
    return bool(_read()["settings"].get("admin_space", True))


# ---------------------------------------------------------------- connexion
def check_credentials(identifier, password):
    """Le compte (dict public) si le mot de passe est bon, sinon None."""
    with _lock:
        data = _read()
        for account in data["accounts"]:
            if account["username"] == (identifier or "").strip().lower():
                if account.get("temporary") and account.get("temporary_exp", 0) < time.time():
                    return None
                if hmac.compare_digest(_password_hash(password or "", account["salt"]), account["fingerprint"]):
                    account["last_login"] = int(time.time())
                    _write(data)
                    return _public_view(account)
                return None
    return None


# ---------------------------------------------------------------- gestion
def _check_identifier(identifier, forbidden):
    identifier = (identifier or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,30}", identifier):
        raise Refusal(400, "Identifiant : 2 à 31 caractères, lettres sans accent, chiffres, point, tiret.")
    if identifier in forbidden:
        raise Refusal(409, "Cet identifiant est réservé au compte du serveur.")
    return identifier


def _check_login(login):
    login = (login or "").strip().lower()
    if login and not re.fullmatch(r"[^\s@]+@[^\s@]+|[\w.-]+", login):
        raise Refusal(
            400, "Compte Tailscale invalide : c'est l'adresse avec laquelle la personne se connecte à Tailscale."
        )
    return login or None


def create(identifier_value, name, rank, tailscale, by_identifier, actor_role, forbidden):
    if rank not in ROLES:
        raise Refusal(400, "Rang inconnu.")
    if rank == "super" and actor_role != "super":
        raise Refusal(403, "Seul un super admin peut créer un super admin.")
    identifier = _check_identifier(identifier_value, forbidden)
    login = _check_login(tailscale)
    password = temporary_password()
    salt = secrets.token_hex(16)
    with _lock:
        data = _read()
        if any(account["username"] == identifier for account in data["accounts"]):
            raise Refusal(409, "Cet identifiant existe déjà.")
        account = {
            "id": secrets.token_hex(4),
            "username": identifier,
            "name": (name or identifier).strip()[:60],
            "rank": rank,
            "tailscale": login,
            "salt": salt,
            "fingerprint": _password_hash(password, salt),
            "temporary": True,
            "temporary_exp": time.time() + RESET_LIFETIME,
            "created": int(time.time()),
            "created_by": by_identifier,
            "last_login": None,
        }
        data["accounts"].append(account)
        _write(data)
    if login:
        _pending_resets[login] = {
            "username": identifier,
            "password": password,
            "exp": time.time() + RESET_LIFETIME,
        }
    return _public_view(account), password


def _account(data, cid):
    for account in data["accounts"]:
        if account["id"] == cid:
            return account
    raise Refusal(404, "Compte introuvable.")


def update(account_id, actor_role, role=None, tailscale=False, display_name=None):
    with _lock:
        data = _read()
        account = _account(data, account_id)
        if (account["rank"] == "super" or role == "super") and actor_role != "super":
            raise Refusal(403, "Seul un super admin peut modifier un super admin.")
        if role is not None:
            if role not in ROLES:
                raise Refusal(400, "Rang inconnu.")
            account["rank"] = role
        if tailscale is not False:
            account["tailscale"] = _check_login(tailscale)
        if display_name is not None:
            account["name"] = display_name.strip()[:60] or account["username"]
        _write(data)
        return _public_view(account)


def delete(cid, actor_role):
    with _lock:
        data = _read()
        account = _account(data, cid)
        if account["rank"] == "super" and actor_role != "super":
            raise Refusal(403, "Seul un super admin peut retirer un super admin.")
        data["accounts"] = [entry for entry in data["accounts"] if entry["id"] != cid]
        _write(data)
        _pending_resets.pop(account.get("tailscale"), None)
        return _public_view(account)


def change_own_password(identifier, new_value):
    account = find(identifier) or {}
    check_password_strength(new_value, (identifier, account.get("name", "")))
    with _lock:
        data = _read()
        for account in data["accounts"]:
            if account["username"] == identifier:
                account["salt"] = secrets.token_hex(16)
                account["fingerprint"] = _password_hash(new_value, account["salt"])
                account["temporary"] = False
                account.pop("temporary_exp", None)
                _write(data)
                return
    raise Refusal(404, "Compte introuvable.")


def set_settings(admin_space):
    with _lock:
        data = _read()
        data["settings"]["admin_space"] = bool(admin_space)
        _write(data)


# ---------------------------------------------------------------- mot de passe oublié
def request_reset(identifier, login, name):
    """Enregistre la demande. Renvoie le compte concerné, ou None (sans le dire à l'appelant)."""
    with _lock:
        data = _read()
        account = next(
            (entry for entry in data["accounts"] if entry["username"] == (identifier or "").strip().lower()), None
        )
        # Seul le compte Tailscale relié au compte peut demander : le mot de passe provisoire
        # ne doit jamais pouvoir arriver chez quelqu'un d'autre (un autre admin, par exemple).
        # Compte non relié : pas de demande possible, il faut voir un super admin.
        if not account or not account.get("tailscale") or account["tailscale"] != login:
            return None
        data["requests"] = [
            entry
            for entry in data["requests"]
            if not (entry["account"] == account["id"] and entry["state"] == "attente")
        ]
        data["requests"].append(
            {
                "id": secrets.token_hex(4),
                "account": account["id"],
                "username": account["username"],
                "name": account["name"],
                "login": login,
                "requester": name,
                "t": int(time.time()),
                "state": "attente",
                "other_person": False,
            }
        )
        data["requests"] = data["requests"][-50:]
        _write(data)
        return _public_view(account)


def answer_reset(did, accept, by_identifier):
    with _lock:
        data = _read()
        reset_request = next(
            (entry for entry in data["requests"] if entry["id"] == did and entry["state"] == "attente"), None
        )
        if not reset_request:
            raise Refusal(404, "Demande introuvable ou déjà traitée.")
        reset_request["state"] = "acceptee" if accept else "refusee"
        reset_request["by"] = by_identifier
        password = None
        if accept:
            account = _account(data, reset_request["account"])
            password = temporary_password()
            account["salt"] = secrets.token_hex(16)
            account["fingerprint"] = _password_hash(password, account["salt"])
            account["temporary"] = True
            account["temporary_exp"] = time.time() + RESET_LIFETIME
            # Remis au compte Tailscale relié au compte, et à lui seul.
            if not account.get("tailscale"):
                raise Refusal(409, "Ce compte n'est plus relié à un compte Tailscale : supprime-le et recrée-le.")
            reset_request["reset_to"] = account["tailscale"]
            _pending_resets[reset_request["reset_to"]] = {
                "username": account["username"],
                "password": password,
                "exp": time.time() + RESET_LIFETIME,
            }
        _write(data)
        return reset_request, password


def pending_reset(login):
    result = _pending_resets.get(login)
    return bool(result and result["exp"] > time.time())


def collect_reset(login):
    """Le mot de passe provisoire, une seule fois, pour la personne qui l'attend."""
    result = _pending_resets.pop(login, None)
    if result and result["exp"] > time.time():
        return {"username": result["username"], "password": result["password"]}
    return None


# ---------------------------------------------------------------- qui se connecte
def record_visit(login, name, device):
    now = int(time.time())
    with _lock:
        if not _visits:
            _visits.update(_read()["visits"])
        visit = _visits.setdefault(login, {"name": name, "first": now, "n": 0})
        new_session = now - visit.get("latest", 0) > 30 * 60
        visit.update(name=name, latest=now)
        if device:
            visit["device"] = device
        if new_session:
            visit["n"] = visit.get("n", 0) + 1
        if new_session or now - _last_flush[0] > 300:
            _last_flush[0] = now
            data = _read()
            data["visits"] = _visits
            _write(data)
        return new_session


def record_refusal(login, device):
    """Garde les 50 dernières tentatives refusées. True si c'est la première depuis 1 h pour ce compte."""
    now = int(time.time())
    with _lock:
        data = _read()
        recent = any(result["login"] == login and now - result["t"] < 3600 for result in data["refusals"])
        if recent:
            return False
        data["refusals"] = (data["refusals"] + [{"login": login, "device": device, "t": now}])[-50:]
        _write(data)
        return True


def recent_logins():
    with _lock:
        data = _read()
        visits = dict(data["visits"])
        visits.update(_visits)
        return {
            "visits": sorted(
                ({"login": key, **visit} for key, visit in visits.items()), key=lambda entry: -entry.get("latest", 0)
            ),
            "refusals": list(reversed(data["refusals"]))[:20],
        }
