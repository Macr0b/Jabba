"""Données de l'accueil : favoris et dossiers souvent ouverts (suggestions).

Sommaire :
  read / write : le fichier accueil.json
  record_visit : note l'ouverture d'un dossier (pour les suggestions)
"""

import json
import os
import threading
import time

# ---------------------------------------------------------------- accueil : essentiels, favoris, suggestions
HOME_FILE = os.environ.get("JABBA_DONNEES", "/data/accueil.json")
LOCK = threading.Lock()
SUGGESTION_THRESHOLD = 5  # ouvertures…
SUGGESTION_WINDOW = 30 * 86400  # …sur 30 jours


def read():
    try:
        with open(HOME_FILE) as file:
            data = json.load(file)
    except (OSError, ValueError):
        data = {}
    data.setdefault("favoris", [])
    data.setdefault("visites", {})
    data.setdefault("ignores", [])
    return data


def write(data):
    os.makedirs(os.path.dirname(HOME_FILE), exist_ok=True)
    temporary_file = HOME_FILE + ".tmp"
    with open(temporary_file, "w") as file:
        json.dump(data, file, ensure_ascii=False)
    os.replace(temporary_file, HOME_FILE)


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
