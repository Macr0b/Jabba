"""Les identifiants des outils de l'Établi, gardés par Jabba (menu « … » → Identifiants, super admin).

Deux sortes de comptes :
  - créés par Jabba : AdGuard Home, Paperless et Time Machine ont besoin d'un compte dès
    l'installation ; Jabba tire au sort le mot de passe (rien à taper). Le même est repris si
    l'outil est retiré puis rallumé (ses données sont gardées). Jabba s'en sert lui-même : on ne
    peut pas le changer d'ici.
  - notés par toi : pour un outil où tu as créé ton compte (Jellyfin, Immich, Homebox…), tu
    enregistres une fois l'identifiant et le mot de passe, et Jabba les garde pour toi.
Tout est dans un fichier lisible par l'appli seule.

Sommaire :
  STORABLE            : les outils dont Jabba peut garder les identifiants
  password_for(tool)  : le mot de passe d'un compte créé par Jabba, tiré au sort la première fois
  read(tool)          : {"identifiant", "mot_de_passe", "cree_par_jabba"} ou None
  created_by_jabba(t) : le compte de l'outil est-il créé (et utilisé) par Jabba ?
  interne : _login_name (le compte du serveur pour Nextcloud et Jellyfin)
  save(tool, identifiant, mot_de_passe) / forget(tool) : les identifiants notés par toi
  drop_unused_password(tool) : oublie un mot de passe tiré pour rien (réinstallation d'un outil installé à la main)
  flag / set_flag     : ce que Jabba a déjà réglé une fois dans un outil (ex. le filtrage général d'AdGuard)
  interne : _check_storable
"""

import os
import re
import secrets
import threading

import config
from host import catalog, commands
from services import json_files

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
CREDENTIALS_FILE = os.path.join(DATA_DIR, "identifiants-outils.json")
_LOCK = threading.Lock()
# Les outils qui ont une page où l'on se connecte (pas l'appli Jabba elle-même, ni les adresses).
STORABLE = frozenset({"jellyfin", "nextcloud", "dockge", *catalog.OPTIONAL_TOOLS})
# Pas de caractère de contrôle ; des longueurs raisonnables.
LOGIN_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,200}")
PASSWORD_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,500}")


def _login_name(tool):
    """L'identifiant du compte créé par Jabba : fixé dans le catalogue, ou le compte du serveur
    (Nextcloud, Jellyfin : l'admin y porte le nom de ton compte sur le serveur)."""
    name = catalog.OPTIONAL_TOOLS[tool].get("username", "admin")
    if name != catalog.SERVER_USER_MARK:
        return name
    if config.DEMO or config.ADMIN_USER:
        return config.ADMIN_USER or "demo"
    _, output, _ = commands.run_action("compte_serveur")
    return output.split(":", 1)[0].strip() or "admin"


def created_by_jabba(tool):
    return bool(catalog.OPTIONAL_TOOLS.get(tool, {}).get("password"))


def password_for(tool):
    with _LOCK:
        saved = json_files.load(CREDENTIALS_FILE, {})
        if not isinstance(saved.get(tool), str) or not saved[tool]:
            saved[tool] = secrets.token_urlsafe(18)  # 24 caractères : lettres, chiffres, - et _
            json_files.save(CREDENTIALS_FILE, saved, private=True)
        return saved[tool]


def read(tool):
    if created_by_jabba(tool):
        identifiant = _login_name(tool)
        password = "demo-" + tool if config.DEMO else json_files.load(CREDENTIALS_FILE, {}).get(tool)
        if not isinstance(password, str) or not password:
            return None
        return {"username": identifiant, "password": password, "created_by_jabba": True}
    noted = json_files.load(CREDENTIALS_FILE, {}).get(tool) if tool in STORABLE else None
    if not isinstance(noted, dict):
        return None
    return {"username": noted["username"], "password": noted["password"], "created_by_jabba": False}


def _check_storable(tool):
    if tool not in STORABLE:
        raise ValueError("Jabba ne garde pas d'identifiants pour cet outil.")
    if created_by_jabba(tool):
        raise ValueError("Ce compte a été créé par Jabba, qui s'en sert lui-même : il ne se change pas d'ici.")


def save(tool, identifiant, mot_de_passe):
    """Note l'identifiant et le mot de passe que tu as choisis dans l'outil. Lève ValueError si refusés."""
    _check_storable(tool)
    identifiant = identifiant.strip() if isinstance(identifiant, str) else ""
    if not LOGIN_TEXT.fullmatch(identifiant):
        raise ValueError("Identifiant manquant ou trop long (200 caractères au plus).")
    if not isinstance(mot_de_passe, str) or not PASSWORD_TEXT.fullmatch(mot_de_passe):
        raise ValueError("Mot de passe manquant ou trop long (500 caractères au plus).")
    with _LOCK:
        saved = json_files.load(CREDENTIALS_FILE, {})
        saved[tool] = {"username": identifiant, "password": mot_de_passe}
        json_files.save(CREDENTIALS_FILE, saved, private=True)


def drop_unused_password(tool):
    """Le mot de passe tiré pour une installation qui a gardé les réglages de la fois d'avant (un outil
    installé à la main avant que Jabba s'en charge) : il ne correspond à rien, on l'oublie."""
    if not created_by_jabba(tool):
        return
    with _LOCK:
        saved = json_files.load(CREDENTIALS_FILE, {})
        if saved.pop(tool, None) is not None:
            json_files.save(CREDENTIALS_FILE, saved, private=True)


def forget(tool):
    _check_storable(tool)
    with _LOCK:
        saved = json_files.load(CREDENTIALS_FILE, {})
        if saved.pop(tool, None) is not None:
            json_files.save(CREDENTIALS_FILE, saved, private=True)


def flag(tool, name):
    return bool(json_files.load(CREDENTIALS_FILE, {}).get(f"{tool}:{name}"))


def set_flag(tool, name):
    with _LOCK:
        saved = json_files.load(CREDENTIALS_FILE, {})
        saved[f"{tool}:{name}"] = True
        json_files.save(CREDENTIALS_FILE, saved, private=True)
