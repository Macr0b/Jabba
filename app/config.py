"""Réglages de l'appli, lus une fois au démarrage (fichier .env du serveur, voir compose.yaml).

Sommaire :
  1. dossiers du code
  2. mode démo (tests)
  3. Tailscale : qui peut ouvrir l'appli
  4. Nextcloud
  5. dépôt GitHub (mises à jour)
  6. divers (batterie, liens)
  read_links  : liens supplémentaires de l'accueil : LIENS="Nom=https://…,Autre=https://…"
  read_nextcloud_link : le compte et le mot de passe d'application de Jabba dans Nextcloud, s'il l'a installé
"""

import json
import os

# ---------------------------------------------------------------- 1. dossiers du code
# Dossier racine du code de l'appli (app/ dans le dépôt, /app dans le conteneur).
APP_DIR = os.path.dirname(os.path.abspath(__file__))
# Pages, styles et scripts de l'interface.
STATIC_DIR = os.path.join(APP_DIR, "static")

# ---------------------------------------------------------------- 2. mode démo
# JABBA_DEMO=1 : données fictives, aucune commande sur le serveur (tests).
DEMO = os.environ.get("JABBA_DEMO") == "1"

# ---------------------------------------------------------------- 3. Tailscale
# Comptes Tailscale qui peuvent ouvrir l'appli, en plus de ceux reliés à un compte admin.
TAILSCALE_USERS = {user.strip().lower() for user in os.environ.get("TS_USERS", "").split(",") if user.strip()}
# Compte du serveur (celui de sudo) : son mot de passe est celui du super admin principal.
ADMIN_USER = os.environ.get("ADMIN_USER", "").strip()

# ---------------------------------------------------------------- 4. Nextcloud
NC_URL = os.environ.get("NC_URL", "http://127.0.0.1:8081").rstrip("/")
NC_HOST = os.environ.get("NC_HOST", "")
# Le lien de Jabba avec Nextcloud : dans le .env (installation à la main, avant la 0.16.6), ou dans ce
# fichier, écrit par Jabba quand il installe Nextcloud lui-même (services/tool_setup.py).
NC_LINK_FILE = os.path.join(os.environ.get("JABBA_DATA", "/data"), "nextcloud-jabba.json")


def read_nextcloud_link():
    """{"utilisateur", "mot_de_passe_appli"} gardés par Jabba, ou {} s'il n'a pas installé Nextcloud lui-même."""
    try:
        with open(NC_LINK_FILE) as file:
            link = json.load(file)
    except (OSError, ValueError):
        return {}
    return link if isinstance(link, dict) else {}


NC_USER = os.environ.get("NC_USER", "") or read_nextcloud_link().get("utilisateur", "")
NC_APP_PASSWORD = os.environ.get("NC_APP_PASSWORD", "") or read_nextcloud_link().get("mot_de_passe_appli", "")
NC_PUBLIC = os.environ.get("NC_PUBLIC_URL", "").rstrip("/")

# ---------------------------------------------------------------- 5. dépôt GitHub
# « propriétaire/dépôt » d'où viennent les mises à jour de l'appli.
REPOSITORY = os.environ.get("JABBA_DEPOT", "").strip()

# ---------------------------------------------------------------- 6. divers
# En dessous de ce pourcentage de batterie (sur secteur coupé), alerte.
BATTERY_THRESHOLD = int(os.environ.get("SEUIL_BATTERIE", "15"))


def read_links():
    """Liens supplémentaires de l'accueil : LIENS="Nom=https://…,Autre=https://…"."""
    links = {}
    for entry in os.environ.get("LIENS", "").split(","):
        if "=" in entry:
            name, url = entry.split("=", 1)
            links[name.strip()] = url.strip()
    return links


LINKS = read_links()
