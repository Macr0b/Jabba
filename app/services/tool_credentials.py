"""Les identifiants des outils de l'Établi, choisis par Jabba à l'activation (rien à taper).

AdGuard Home, Paperless et Time Machine ont besoin d'un compte dès l'installation : Jabba tire
au sort un mot de passe, le garde dans un fichier lisible par l'appli seule, et le montre au
super admin (menu « … » → Identifiants) quand il veut ouvrir l'outil ou y brancher un Mac.
Le même mot de passe est repris si l'outil est retiré puis rallumé (ses données sont gardées).

Sommaire :
  password_for(tool)  : le mot de passe de l'outil, tiré au sort la première fois
  read(tool)          : {"identifiant", "mot_de_passe"} ou None
"""

import os
import secrets
import threading

import config
from host import catalog
from services import json_files

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
CREDENTIALS_FILE = os.path.join(DATA_DIR, "identifiants-outils.json")
_LOCK = threading.Lock()


def password_for(tool):
    with _LOCK:
        saved = json_files.load(CREDENTIALS_FILE, {})
        if not saved.get(tool):
            saved[tool] = secrets.token_urlsafe(18)  # 24 caractères : lettres, chiffres, - et _
            json_files.save(CREDENTIALS_FILE, saved, private=True)
        return saved[tool]


def read(tool):
    if config.DEMO and tool in catalog.OPTIONAL_TOOLS and catalog.OPTIONAL_TOOLS[tool]["mot_de_passe"]:
        return {"identifiant": catalog.OPTIONAL_TOOLS[tool].get("identifiant", "admin"), "mot_de_passe": "demo-" + tool}
    password = json_files.load(CREDENTIALS_FILE, {}).get(tool)
    if not password:
        return None
    return {"identifiant": catalog.OPTIONAL_TOOLS.get(tool, {}).get("identifiant", "admin"), "mot_de_passe": password}
