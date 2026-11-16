"""Emplacements fixes de l'appli (dossiers du code), communs à tous les modules."""
import os

# Dossier racine du code de l'appli (app/ dans le dépôt, /app dans le conteneur).
APP_DIR = os.path.dirname(os.path.abspath(__file__))
# Pages, styles et scripts de l'interface.
STATIC_DIR = os.path.join(APP_DIR, "static")
