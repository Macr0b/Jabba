"""Emplacements fixes de l'appli (dossiers du code), communs à tous les modules."""
import os

# Dossier racine du code de l'appli (app/ dans le dépôt, /app dans le conteneur).
APP_DIR = os.path.dirname(os.path.abspath(__file__))
# Pages, styles et scripts de l'interface.
STATIC_DIR = os.path.join(APP_DIR, "static")


# ---- ajouté au découpage de main.py
import os
import config



DEMO = os.environ.get("JABBA_DEMO") == "1"
TAILSCALE_USERS = {u.strip().lower() for u in os.environ.get("TS_USERS", "").split(",") if u.strip()}
NC_URL = os.environ.get("NC_URL", "http://127.0.0.1:8081").rstrip("/")
NC_HOST = os.environ.get("NC_HOST", "")
NC_USER = os.environ.get("NC_USER", "")
NC_APP_PASSWORD = os.environ.get("NC_APP_PASSWORD", "")
NC_PUBLIC = os.environ.get("NC_PUBLIC_URL", "").rstrip("/")
STATIC = config.STATIC_DIR
ADMIN_USER = os.environ.get("ADMIN_USER", "").strip()
REPOSITORY = os.environ.get("JABBA_DEPOT", "").strip()
BATTERY_THRESHOLD = int(os.environ.get("SEUIL_BATTERIE", "15"))


def read_links():
    links = {}
    for share in os.environ.get("LIENS", "").split(","):
        if "=" in share:
            name, url = share.split("=", 1)
            links[name.strip()] = url.strip()
    return links


LINKS = read_links()
