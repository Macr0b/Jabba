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

DOSSIER = os.environ.get("JABBA_DATA", "/data")
FICHIER = os.path.join(DOSSIER, "comptes.json")
RANGS = ("admin", "super")
DUREE_REMISE = 24 * 3600
_verrou = threading.RLock()
_remises = {}   # login Tailscale -> {"identifiant", "mot_de_passe", "exp"}
_visites = {}   # login -> {"nom", "appareil", "derniere", "premiere", "n"} (copie en mémoire)
_dernier_flush = [0]


class Refus(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _lire():
    try:
        with open(FICHIER) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("comptes", [])
    d.setdefault("reglages", {"espace_admins": True})
    d.setdefault("demandes", [])
    d.setdefault("visites", {})
    d.setdefault("refus", [])
    return d


def _ecrire(d):
    os.makedirs(DOSSIER, exist_ok=True)
    tmp = FICHIER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, FICHIER)


def _empreinte(mdp, sel):
    return hashlib.scrypt(mdp.encode(), salt=bytes.fromhex(sel), n=2 ** 14, r=8, p=1).hex()


def _public(c):
    r = {k: c.get(k) for k in ("id", "identifiant", "nom", "rang", "tailscale", "provisoire", "cree", "cree_par", "derniere_connexion")}
    r["terminal"] = c.get("terminal", True)   # les super admins ont le terminal, sauf s'il leur est retiré
    return r


def valider_mot_de_passe(mdp):
    if len(mdp or "") < 10:
        raise Refus(400, "Le mot de passe doit faire au moins 10 caractères.")
    if any(c in mdp for c in "\n\r"):
        raise Refus(400, "Le mot de passe ne peut pas contenir de retour à la ligne.")


def provisoire():
    """Mot de passe provisoire lisible : 3 mots et 2 chiffres."""
    from admin import MOTS
    return "-".join(secrets.choice(MOTS) for _ in range(3)) + "-" + str(secrets.randbelow(90) + 10)


# ---------------------------------------------------------------- lecture
def lister():
    with _verrou:
        d = _lire()
        return {"comptes": [_public(c) for c in d["comptes"]], "reglages": d["reglages"],
                "demandes": [x for x in d["demandes"] if x["etat"] == "attente"]}


def trouver(identifiant):
    ident = (identifiant or "").strip().lower()
    for c in _lire()["comptes"]:
        if c["identifiant"] == ident:
            return c
    return None


def logins_autorises():
    """Comptes Tailscale reliés à un compte de l'appli : ils peuvent ouvrir l'appli."""
    return {c["tailscale"] for c in _lire()["comptes"] if c.get("tailscale")}


def logins_super():
    return {c["tailscale"] for c in _lire()["comptes"] if c.get("tailscale") and c["rang"] == "super"}


def espace_ouvert_aux_admins():
    return bool(_lire()["reglages"].get("espace_admins", True))


# ---------------------------------------------------------------- connexion
def verifier(identifiant, mdp):
    """Le compte (dict public) si le mot de passe est bon, sinon None."""
    with _verrou:
        d = _lire()
        for c in d["comptes"]:
            if c["identifiant"] == (identifiant or "").strip().lower():
                if c.get("provisoire") and c.get("provisoire_exp", 0) < time.time():
                    return None
                if hmac.compare_digest(_empreinte(mdp or "", c["sel"]), c["empreinte"]):
                    c["derniere_connexion"] = int(time.time())
                    _ecrire(d)
                    return _public(c)
                return None
    return None


# ---------------------------------------------------------------- gestion
def _verifier_identifiant(ident, interdits):
    ident = (ident or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,30}", ident):
        raise Refus(400, "Identifiant : 2 à 31 caractères, lettres sans accent, chiffres, point, tiret.")
    if ident in interdits:
        raise Refus(409, "Cet identifiant est réservé au compte du serveur.")
    return ident


def _verifier_login(login):
    login = (login or "").strip().lower()
    if login and not re.fullmatch(r"[^\s@]+@[^\s@]+|[\w.-]+", login):
        raise Refus(400, "Compte Tailscale invalide : c'est l'adresse avec laquelle la personne se connecte à Tailscale.")
    return login or None


def creer(identifiant, nom, rang, tailscale, par, rang_par, interdits):
    if rang not in RANGS:
        raise Refus(400, "Rang inconnu.")
    if rang == "super" and rang_par != "super":
        raise Refus(403, "Seul un super admin peut créer un super admin.")
    ident = _verifier_identifiant(identifiant, interdits)
    login = _verifier_login(tailscale)
    mdp = provisoire()
    sel = secrets.token_hex(16)
    with _verrou:
        d = _lire()
        if any(c["identifiant"] == ident for c in d["comptes"]):
            raise Refus(409, "Cet identifiant existe déjà.")
        c = {"id": secrets.token_hex(4), "identifiant": ident, "nom": (nom or ident).strip()[:60], "rang": rang,
             "tailscale": login, "sel": sel, "empreinte": _empreinte(mdp, sel), "provisoire": True,
             "provisoire_exp": time.time() + DUREE_REMISE, "cree": int(time.time()), "cree_par": par,
             "derniere_connexion": None}
        d["comptes"].append(c)
        _ecrire(d)
    if login:
        _remises[login] = {"identifiant": ident, "mot_de_passe": mdp, "exp": time.time() + DUREE_REMISE}
    return _public(c), mdp


def _compte(d, cid):
    for c in d["comptes"]:
        if c["id"] == cid:
            return c
    raise Refus(404, "Compte introuvable.")


def modifier(cid, rang_par, rang=None, tailscale=False, nom=None, terminal=None):
    with _verrou:
        d = _lire()
        c = _compte(d, cid)
        if (c["rang"] == "super" or rang == "super") and rang_par != "super":
            raise Refus(403, "Seul un super admin peut modifier un super admin.")
        if rang is not None:
            if rang not in RANGS:
                raise Refus(400, "Rang inconnu.")
            c["rang"] = rang
        if tailscale is not False:
            c["tailscale"] = _verifier_login(tailscale)
        if nom is not None:
            c["nom"] = nom.strip()[:60] or c["identifiant"]
        if terminal is not None:
            if rang_par != "super":
                raise Refus(403, "Seul un super admin peut régler l'accès au terminal.")
            c["terminal"] = bool(terminal)
        _ecrire(d)
        return _public(c)


def supprimer(cid, rang_par):
    with _verrou:
        d = _lire()
        c = _compte(d, cid)
        if c["rang"] == "super" and rang_par != "super":
            raise Refus(403, "Seul un super admin peut retirer un super admin.")
        d["comptes"] = [x for x in d["comptes"] if x["id"] != cid]
        _ecrire(d)
        _remises.pop(c.get("tailscale"), None)
        return _public(c)


def changer_mien(identifiant, nouveau):
    valider_mot_de_passe(nouveau)
    with _verrou:
        d = _lire()
        for c in d["comptes"]:
            if c["identifiant"] == identifiant:
                c["sel"] = secrets.token_hex(16)
                c["empreinte"] = _empreinte(nouveau, c["sel"])
                c["provisoire"] = False
                c.pop("provisoire_exp", None)
                _ecrire(d)
                return
    raise Refus(404, "Compte introuvable.")


def regler(espace_admins):
    with _verrou:
        d = _lire()
        d["reglages"]["espace_admins"] = bool(espace_admins)
        _ecrire(d)


# ---------------------------------------------------------------- mot de passe oublié
def demander(identifiant, login, nom):
    """Enregistre la demande. Renvoie le compte concerné, ou None (sans le dire à l'appelant)."""
    with _verrou:
        d = _lire()
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
        _ecrire(d)
        return _public(c)


def repondre(did, accepter, par):
    with _verrou:
        d = _lire()
        dem = next((x for x in d["demandes"] if x["id"] == did and x["etat"] == "attente"), None)
        if not dem:
            raise Refus(404, "Demande introuvable ou déjà traitée.")
        dem["etat"] = "acceptee" if accepter else "refusee"
        dem["par"] = par
        mdp = None
        if accepter:
            c = _compte(d, dem["compte"])
            mdp = provisoire()
            c["sel"] = secrets.token_hex(16)
            c["empreinte"] = _empreinte(mdp, c["sel"])
            c["provisoire"] = True
            c["provisoire_exp"] = time.time() + DUREE_REMISE
            # Remis au compte Tailscale relié au compte, et à lui seul.
            if not c.get("tailscale"):
                raise Refus(409, "Ce compte n'est plus relié à un compte Tailscale : supprime-le et recrée-le.")
            dem["remis_a"] = c["tailscale"]
            _remises[dem["remis_a"]] = {"identifiant": c["identifiant"], "mot_de_passe": mdp, "exp": time.time() + DUREE_REMISE}
        _ecrire(d)
        return dem, mdp


def remise_en_attente(login):
    r = _remises.get(login)
    return bool(r and r["exp"] > time.time())


def prendre_remise(login):
    """Le mot de passe provisoire, une seule fois, pour la personne qui l'attend."""
    r = _remises.pop(login, None)
    if r and r["exp"] > time.time():
        return {"identifiant": r["identifiant"], "mot_de_passe": r["mot_de_passe"]}
    return None


# ---------------------------------------------------------------- qui se connecte
def noter_visite(login, nom, appareil):
    maintenant = int(time.time())
    with _verrou:
        if not _visites:
            _visites.update(_lire()["visites"])
        v = _visites.setdefault(login, {"nom": nom, "premiere": maintenant, "n": 0})
        nouvelle_session = maintenant - v.get("derniere", 0) > 30 * 60
        v.update(nom=nom, derniere=maintenant)
        if appareil:
            v["appareil"] = appareil
        if nouvelle_session:
            v["n"] = v.get("n", 0) + 1
        if nouvelle_session or maintenant - _dernier_flush[0] > 300:
            _dernier_flush[0] = maintenant
            d = _lire()
            d["visites"] = _visites
            _ecrire(d)
        return nouvelle_session


def noter_refus(login, appareil):
    """Garde les 50 dernières tentatives refusées. True si c'est la première depuis 1 h pour ce compte."""
    maintenant = int(time.time())
    with _verrou:
        d = _lire()
        recent = any(r["login"] == login and maintenant - r["t"] < 3600 for r in d["refus"])
        if recent:
            return False
        d["refus"] = (d["refus"] + [{"login": login, "appareil": appareil, "t": maintenant}])[-50:]
        _ecrire(d)
        return True


def connexions():
    with _verrou:
        d = _lire()
        visites = dict(d["visites"])
        visites.update(_visites)
        return {"visites": sorted(({"login": k, **v} for k, v in visites.items()), key=lambda x: -x.get("derniere", 0)),
                "refus": list(reversed(d["refus"]))[:20]}
