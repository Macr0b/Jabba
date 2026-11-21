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
  Refusal
  check_password_strength  : applique les règles de security.password_rules (15 caractères, pas de mot courant…)
  temporary_password       : mot de passe provisoire lisible : 3 mots et 2 chiffres
  list_accounts
  find
  allowed_logins           : comptes Tailscale reliés à un compte de l'appli : ils peuvent ouvrir l'appli
  super_admin_logins
  admin_space_open
  check_credentials        : le compte (dict public) si le mot de passe est bon, sinon None
  create
  update
  delete
  change_own_password
  set_settings
  request_reset            : enregistre la demande. Renvoie le compte concerné, ou None (sans le dire à l'appelant)
  answer_reset
  pending_reset
  collect_reset            : le mot de passe provisoire, une seule fois, pour la personne qui l'attend
  record_visit
  record_refusal           : garde les 50 dernières tentatives refusées. True si c'est la première depuis 1 h pour ce compte
  recent_logins
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
    data.setdefault("comptes", [])
    data.setdefault("reglages", {"espace_admins": True})
    data.setdefault("demandes", [])
    data.setdefault("visites", {})
    data.setdefault("refus", [])
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
            "identifiant",
            "nom",
            "rang",
            "tailscale",
            "provisoire",
            "cree",
            "cree_par",
            "derniere_connexion",
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
            "comptes": [_public_view(account) for account in data["comptes"]],
            "reglages": data["reglages"],
            "demandes": [entry for entry in data["demandes"] if entry["etat"] == "attente"],
        }


def find(identifier_value):
    identifier = (identifier_value or "").strip().lower()
    for account in _read()["comptes"]:
        if account["identifiant"] == identifier:
            return account
    return None


def allowed_logins():
    """Comptes Tailscale reliés à un compte de l'appli : ils peuvent ouvrir l'appli."""
    return {account["tailscale"] for account in _read()["comptes"] if account.get("tailscale")}


def super_admin_logins():
    return {
        account["tailscale"]
        for account in _read()["comptes"]
        if account.get("tailscale") and account["rang"] == "super"
    }


def admin_space_open():
    return bool(_read()["reglages"].get("espace_admins", True))


# ---------------------------------------------------------------- connexion
def check_credentials(identifier, password):
    """Le compte (dict public) si le mot de passe est bon, sinon None."""
    with _lock:
        data = _read()
        for account in data["comptes"]:
            if account["identifiant"] == (identifier or "").strip().lower():
                if account.get("provisoire") and account.get("provisoire_exp", 0) < time.time():
                    return None
                if hmac.compare_digest(_password_hash(password or "", account["sel"]), account["empreinte"]):
                    account["derniere_connexion"] = int(time.time())
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
        if any(account["identifiant"] == identifier for account in data["comptes"]):
            raise Refusal(409, "Cet identifiant existe déjà.")
        account = {
            "id": secrets.token_hex(4),
            "identifiant": identifier,
            "nom": (name or identifier).strip()[:60],
            "rang": rank,
            "tailscale": login,
            "sel": salt,
            "empreinte": _password_hash(password, salt),
            "provisoire": True,
            "provisoire_exp": time.time() + RESET_LIFETIME,
            "cree": int(time.time()),
            "cree_par": by_identifier,
            "derniere_connexion": None,
        }
        data["comptes"].append(account)
        _write(data)
    if login:
        _pending_resets[login] = {
            "identifiant": identifier,
            "mot_de_passe": password,
            "exp": time.time() + RESET_LIFETIME,
        }
    return _public_view(account), password


def _account(data, cid):
    for account in data["comptes"]:
        if account["id"] == cid:
            return account
    raise Refusal(404, "Compte introuvable.")


def update(account_id, actor_role, role=None, tailscale=False, display_name=None):
    with _lock:
        data = _read()
        account = _account(data, account_id)
        if (account["rang"] == "super" or role == "super") and actor_role != "super":
            raise Refusal(403, "Seul un super admin peut modifier un super admin.")
        if role is not None:
            if role not in ROLES:
                raise Refusal(400, "Rang inconnu.")
            account["rang"] = role
        if tailscale is not False:
            account["tailscale"] = _check_login(tailscale)
        if display_name is not None:
            account["nom"] = display_name.strip()[:60] or account["identifiant"]
        _write(data)
        return _public_view(account)


def delete(cid, actor_role):
    with _lock:
        data = _read()
        account = _account(data, cid)
        if account["rang"] == "super" and actor_role != "super":
            raise Refusal(403, "Seul un super admin peut retirer un super admin.")
        data["comptes"] = [entry for entry in data["comptes"] if entry["id"] != cid]
        _write(data)
        _pending_resets.pop(account.get("tailscale"), None)
        return _public_view(account)


def change_own_password(identifier, new_value):
    account = find(identifier) or {}
    check_password_strength(new_value, (identifier, account.get("nom", "")))
    with _lock:
        data = _read()
        for account in data["comptes"]:
            if account["identifiant"] == identifier:
                account["sel"] = secrets.token_hex(16)
                account["empreinte"] = _password_hash(new_value, account["sel"])
                account["provisoire"] = False
                account.pop("provisoire_exp", None)
                _write(data)
                return
    raise Refusal(404, "Compte introuvable.")


def set_settings(admin_space):
    with _lock:
        data = _read()
        data["reglages"]["espace_admins"] = bool(admin_space)
        _write(data)


# ---------------------------------------------------------------- mot de passe oublié
def request_reset(identifier, login, name):
    """Enregistre la demande. Renvoie le compte concerné, ou None (sans le dire à l'appelant)."""
    with _lock:
        data = _read()
        account = next(
            (entry for entry in data["comptes"] if entry["identifiant"] == (identifier or "").strip().lower()), None
        )
        # Seul le compte Tailscale relié au compte peut demander : le mot de passe provisoire
        # ne doit jamais pouvoir arriver chez quelqu'un d'autre (un autre admin, par exemple).
        # Compte non relié : pas de demande possible, il faut voir un super admin.
        if not account or not account.get("tailscale") or account["tailscale"] != login:
            return None
        data["demandes"] = [
            entry for entry in data["demandes"] if not (entry["compte"] == account["id"] and entry["etat"] == "attente")
        ]
        data["demandes"].append(
            {
                "id": secrets.token_hex(4),
                "compte": account["id"],
                "identifiant": account["identifiant"],
                "nom": account["nom"],
                "login": login,
                "demandeur": name,
                "t": int(time.time()),
                "etat": "attente",
                "autre_personne": False,
            }
        )
        data["demandes"] = data["demandes"][-50:]
        _write(data)
        return _public_view(account)


def answer_reset(did, accept, by_identifier):
    with _lock:
        data = _read()
        reset_request = next(
            (entry for entry in data["demandes"] if entry["id"] == did and entry["etat"] == "attente"), None
        )
        if not reset_request:
            raise Refusal(404, "Demande introuvable ou déjà traitée.")
        reset_request["etat"] = "acceptee" if accept else "refusee"
        reset_request["par"] = by_identifier
        password = None
        if accept:
            account = _account(data, reset_request["compte"])
            password = temporary_password()
            account["sel"] = secrets.token_hex(16)
            account["empreinte"] = _password_hash(password, account["sel"])
            account["provisoire"] = True
            account["provisoire_exp"] = time.time() + RESET_LIFETIME
            # Remis au compte Tailscale relié au compte, et à lui seul.
            if not account.get("tailscale"):
                raise Refusal(409, "Ce compte n'est plus relié à un compte Tailscale : supprime-le et recrée-le.")
            reset_request["remis_a"] = account["tailscale"]
            _pending_resets[reset_request["remis_a"]] = {
                "identifiant": account["identifiant"],
                "mot_de_passe": password,
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
        return {"identifiant": result["identifiant"], "mot_de_passe": result["mot_de_passe"]}
    return None


# ---------------------------------------------------------------- qui se connecte
def record_visit(login, name, device):
    now = int(time.time())
    with _lock:
        if not _visits:
            _visits.update(_read()["visites"])
        visit = _visits.setdefault(login, {"nom": name, "premiere": now, "n": 0})
        new_session = now - visit.get("derniere", 0) > 30 * 60
        visit.update(nom=name, derniere=now)
        if device:
            visit["appareil"] = device
        if new_session:
            visit["n"] = visit.get("n", 0) + 1
        if new_session or now - _last_flush[0] > 300:
            _last_flush[0] = now
            data = _read()
            data["visites"] = _visits
            _write(data)
        return new_session


def record_refusal(login, device):
    """Garde les 50 dernières tentatives refusées. True si c'est la première depuis 1 h pour ce compte."""
    now = int(time.time())
    with _lock:
        data = _read()
        recent = any(result["login"] == login and now - result["t"] < 3600 for result in data["refus"])
        if recent:
            return False
        data["refus"] = (data["refus"] + [{"login": login, "appareil": device, "t": now}])[-50:]
        _write(data)
        return True


def recent_logins():
    with _lock:
        data = _read()
        visits = dict(data["visites"])
        visits.update(_visits)
        return {
            "visites": sorted(
                ({"login": key, **visit} for key, visit in visits.items()), key=lambda entry: -entry.get("derniere", 0)
            ),
            "refus": list(reversed(data["refus"]))[:20],
        }
