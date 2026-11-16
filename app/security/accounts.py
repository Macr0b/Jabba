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
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "comptes.json")
ROLES = ("admin", "super")
RESET_LIFETIME = 24 * 3600
_lock = threading.RLock()
_pending_resets = {}   # login Tailscale -> {"identifiant", "mot_de_passe", "exp"}
_visits = {}   # login -> {"nom", "appareil", "derniere", "premiere", "n"} (copie en mémoire)
_last_flush = [0]


class Refusal(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _read():
    try:
        with open(ACCOUNTS_FILE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("comptes", [])
    d.setdefault("reglages", {"espace_admins": True})
    d.setdefault("demandes", [])
    d.setdefault("visites", {})
    d.setdefault("refus", [])
    return d


def _write(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = ACCOUNTS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ACCOUNTS_FILE)


def _password_hash(mdp, sel):
    return hashlib.scrypt(mdp.encode(), salt=bytes.fromhex(sel), n=2 ** 14, r=8, p=1).hex()


def _public_view(c):
    r = {k: c.get(k) for k in ("id", "identifiant", "nom", "rang", "tailscale", "provisoire", "cree", "cree_par", "derniere_connexion")}
    r["terminal"] = c.get("terminal", True)   # les super admins ont le terminal, sauf s'il leur est retiré
    return r


def check_password_strength(mdp):
    if len(mdp or "") < 10:
        raise Refusal(400, "Le mot de passe doit faire au moins 10 caractères.")
    if any(c in mdp for c in "\n\r"):
        raise Refusal(400, "Le mot de passe ne peut pas contenir de retour à la ligne.")


def temporary_password():
    """Mot de passe provisoire lisible : 3 mots et 2 chiffres."""
    from security.server_password import WORDS
    return "-".join(secrets.choice(WORDS) for _ in range(3)) + "-" + str(secrets.randbelow(90) + 10)


# ---------------------------------------------------------------- lecture
def list_accounts():
    with _lock:
        d = _read()
        return {"comptes": [_public_view(c) for c in d["comptes"]], "reglages": d["reglages"],
                "demandes": [x for x in d["demandes"] if x["etat"] == "attente"]}


def find(identifiant):
    ident = (identifiant or "").strip().lower()
    for c in _read()["comptes"]:
        if c["identifiant"] == ident:
            return c
    return None


def allowed_logins():
    """Comptes Tailscale reliés à un compte de l'appli : ils peuvent ouvrir l'appli."""
    return {c["tailscale"] for c in _read()["comptes"] if c.get("tailscale")}


def super_admin_logins():
    return {c["tailscale"] for c in _read()["comptes"] if c.get("tailscale") and c["rang"] == "super"}


def admin_space_open():
    return bool(_read()["reglages"].get("espace_admins", True))


# ---------------------------------------------------------------- connexion
def check_credentials(identifiant, mdp):
    """Le compte (dict public) si le mot de passe est bon, sinon None."""
    with _lock:
        d = _read()
        for c in d["comptes"]:
            if c["identifiant"] == (identifiant or "").strip().lower():
                if c.get("provisoire") and c.get("provisoire_exp", 0) < time.time():
                    return None
                if hmac.compare_digest(_password_hash(mdp or "", c["sel"]), c["empreinte"]):
                    c["derniere_connexion"] = int(time.time())
                    _write(d)
                    return _public_view(c)
                return None
    return None


# ---------------------------------------------------------------- gestion
def _check_identifier(ident, interdits):
    ident = (ident or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,30}", ident):
        raise Refusal(400, "Identifiant : 2 à 31 caractères, lettres sans accent, chiffres, point, tiret.")
    if ident in interdits:
        raise Refusal(409, "Cet identifiant est réservé au compte du serveur.")
    return ident


def _check_login(login):
    login = (login or "").strip().lower()
    if login and not re.fullmatch(r"[^\s@]+@[^\s@]+|[\w.-]+", login):
        raise Refusal(400, "Compte Tailscale invalide : c'est l'adresse avec laquelle la personne se connecte à Tailscale.")
    return login or None


def create(identifiant, nom, rang, tailscale, par, rang_par, interdits):
    if rang not in ROLES:
        raise Refusal(400, "Rang inconnu.")
    if rang == "super" and rang_par != "super":
        raise Refusal(403, "Seul un super admin peut créer un super admin.")
    ident = _check_identifier(identifiant, interdits)
    login = _check_login(tailscale)
    mdp = temporary_password()
    sel = secrets.token_hex(16)
    with _lock:
        d = _read()
        if any(c["identifiant"] == ident for c in d["comptes"]):
            raise Refusal(409, "Cet identifiant existe déjà.")
        c = {"id": secrets.token_hex(4), "identifiant": ident, "nom": (nom or ident).strip()[:60], "rang": rang,
             "tailscale": login, "sel": sel, "empreinte": _password_hash(mdp, sel), "provisoire": True,
             "provisoire_exp": time.time() + RESET_LIFETIME, "cree": int(time.time()), "cree_par": par,
             "derniere_connexion": None}
        d["comptes"].append(c)
        _write(d)
    if login:
        _pending_resets[login] = {"identifiant": ident, "mot_de_passe": mdp, "exp": time.time() + RESET_LIFETIME}
    return _public_view(c), mdp


def _account(d, cid):
    for c in d["comptes"]:
        if c["id"] == cid:
            return c
    raise Refusal(404, "Compte introuvable.")


def update(cid, rang_par, rang=None, tailscale=False, nom=None, terminal=None):
    with _lock:
        d = _read()
        c = _account(d, cid)
        if (c["rang"] == "super" or rang == "super") and rang_par != "super":
            raise Refusal(403, "Seul un super admin peut modifier un super admin.")
        if rang is not None:
            if rang not in ROLES:
                raise Refusal(400, "Rang inconnu.")
            c["rang"] = rang
        if tailscale is not False:
            c["tailscale"] = _check_login(tailscale)
        if nom is not None:
            c["nom"] = nom.strip()[:60] or c["identifiant"]
        if terminal is not None:
            if rang_par != "super":
                raise Refusal(403, "Seul un super admin peut régler l'accès au terminal.")
            c["terminal"] = bool(terminal)
        _write(d)
        return _public_view(c)


def delete(cid, rang_par):
    with _lock:
        d = _read()
        c = _account(d, cid)
        if c["rang"] == "super" and rang_par != "super":
            raise Refusal(403, "Seul un super admin peut retirer un super admin.")
        d["comptes"] = [x for x in d["comptes"] if x["id"] != cid]
        _write(d)
        _pending_resets.pop(c.get("tailscale"), None)
        return _public_view(c)


def change_own_password(identifiant, nouveau):
    check_password_strength(nouveau)
    with _lock:
        d = _read()
        for c in d["comptes"]:
            if c["identifiant"] == identifiant:
                c["sel"] = secrets.token_hex(16)
                c["empreinte"] = _password_hash(nouveau, c["sel"])
                c["provisoire"] = False
                c.pop("provisoire_exp", None)
                _write(d)
                return
    raise Refusal(404, "Compte introuvable.")


def set_settings(espace_admins):
    with _lock:
        d = _read()
        d["reglages"]["espace_admins"] = bool(espace_admins)
        _write(d)


# ---------------------------------------------------------------- mot de passe oublié
def request_reset(identifiant, login, nom):
    """Enregistre la demande. Renvoie le compte concerné, ou None (sans le dire à l'appelant)."""
    with _lock:
        d = _read()
        c = next((x for x in d["comptes"] if x["identifiant"] == (identifiant or "").strip().lower()), None)
        # Seul le compte Tailscale relié au compte peut demander : le mot de passe provisoire
        # ne doit jamais pouvoir arriver chez quelqu'un d'autre (un autre admin, par exemple).
        # Compte non relié : pas de demande possible, il faut voir un super admin.
        if not c or not c.get("tailscale") or c["tailscale"] != login:
            return None
        d["demandes"] = [x for x in d["demandes"] if not (x["compte"] == c["id"] and x["etat"] == "attente")]
        d["demandes"].append({"id": secrets.token_hex(4), "compte": c["id"], "identifiant": c["identifiant"],
                              "nom": c["nom"], "login": login, "demandeur": nom, "t": int(time.time()), "etat": "attente",
                              "autre_personne": False})
        d["demandes"] = d["demandes"][-50:]
        _write(d)
        return _public_view(c)


def answer_reset(did, accepter, par):
    with _lock:
        d = _read()
        dem = next((x for x in d["demandes"] if x["id"] == did and x["etat"] == "attente"), None)
        if not dem:
            raise Refusal(404, "Demande introuvable ou déjà traitée.")
        dem["etat"] = "acceptee" if accepter else "refusee"
        dem["par"] = par
        mdp = None
        if accepter:
            c = _account(d, dem["compte"])
            mdp = temporary_password()
            c["sel"] = secrets.token_hex(16)
            c["empreinte"] = _password_hash(mdp, c["sel"])
            c["provisoire"] = True
            c["provisoire_exp"] = time.time() + RESET_LIFETIME
            # Remis au compte Tailscale relié au compte, et à lui seul.
            if not c.get("tailscale"):
                raise Refusal(409, "Ce compte n'est plus relié à un compte Tailscale : supprime-le et recrée-le.")
            dem["remis_a"] = c["tailscale"]
            _pending_resets[dem["remis_a"]] = {"identifiant": c["identifiant"], "mot_de_passe": mdp, "exp": time.time() + RESET_LIFETIME}
        _write(d)
        return dem, mdp


def pending_reset(login):
    r = _pending_resets.get(login)
    return bool(r and r["exp"] > time.time())


def collect_reset(login):
    """Le mot de passe provisoire, une seule fois, pour la personne qui l'attend."""
    r = _pending_resets.pop(login, None)
    if r and r["exp"] > time.time():
        return {"identifiant": r["identifiant"], "mot_de_passe": r["mot_de_passe"]}
    return None


# ---------------------------------------------------------------- qui se connecte
def record_visit(login, nom, appareil):
    maintenant = int(time.time())
    with _lock:
        if not _visits:
            _visits.update(_read()["visites"])
        v = _visits.setdefault(login, {"nom": nom, "premiere": maintenant, "n": 0})
        nouvelle_session = maintenant - v.get("derniere", 0) > 30 * 60
        v.update(nom=nom, derniere=maintenant)
        if appareil:
            v["appareil"] = appareil
        if nouvelle_session:
            v["n"] = v.get("n", 0) + 1
        if nouvelle_session or maintenant - _last_flush[0] > 300:
            _last_flush[0] = maintenant
            d = _read()
            d["visites"] = _visits
            _write(d)
        return nouvelle_session


def record_refusal(login, appareil):
    """Garde les 50 dernières tentatives refusées. True si c'est la première depuis 1 h pour ce compte."""
    maintenant = int(time.time())
    with _lock:
        d = _read()
        recent = any(r["login"] == login and maintenant - r["t"] < 3600 for r in d["refus"])
        if recent:
            return False
        d["refus"] = (d["refus"] + [{"login": login, "appareil": appareil, "t": maintenant}])[-50:]
        _write(d)
        return True


def recent_logins():
    with _lock:
        d = _read()
        visites = dict(d["visites"])
        visites.update(_visits)
        return {"visites": sorted(({"login": k, **v} for k, v in visites.items()), key=lambda x: -x.get("derniere", 0)),
                "refus": list(reversed(d["refus"]))[:20]}
