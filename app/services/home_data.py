"""Données de l'accueil : favoris et dossiers souvent ouverts (suggestions).

Sommaire :
  read / write : le fichier accueil.json
  record_visit : note l'ouverture d'un dossier (pour les suggestions)
"""

import os
import threading
import time

from services import json_files

# ---------------------------------------------------------------- accueil : essentiels, favoris, suggestions
HOME_FILE = os.environ.get("JABBA_DONNEES", "/data/accueil.json")
LOCK = threading.Lock()
SUGGESTION_THRESHOLD = 5  # ouvertures…
SUGGESTION_WINDOW = 30 * 86400  # …sur 30 jours


def read():
    data = json_files.load(HOME_FILE)
    data.setdefault("favoris", [])
    data.setdefault("visites", {})
    data.setdefault("ignores", [])
    return data


def write(data):
    json_files.save(HOME_FILE, data)


def record_visit(path):
    with LOCK:
        data = read()
        cutoff = time.time() - SUGGESTION_WINDOW
        data["visites"] = {
            folder: [timestamp for timestamp in timestamps if timestamp > cutoff]
            for folder, timestamps in data["visites"].items()
            if any(timestamp > cutoff for timestamp in timestamps)
        }
        data["visites"].setdefault(path, []).append(int(time.time()))
        data["visites"][path] = data["visites"][path][-50:]
        write(data)
