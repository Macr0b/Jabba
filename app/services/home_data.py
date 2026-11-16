"""services.home_data"""
import json
import os
import threading
import time



# ---------------------------------------------------------------- accueil : essentiels, favoris, suggestions
HOME_FILE = os.environ.get("JABBA_DONNEES", "/data/accueil.json")
LOCK = threading.Lock()
SUGGESTION_THRESHOLD = 5          # ouvertures…
SUGGESTION_WINDOW = 30 * 86400  # …sur 30 jours


def read():
    try:
        with open(HOME_FILE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("favoris", [])
    d.setdefault("visites", {})
    d.setdefault("ignores", [])
    return d


def write(d):
    os.makedirs(os.path.dirname(HOME_FILE), exist_ok=True)
    tmp = HOME_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, HOME_FILE)


def record_visit(chemin):
    with LOCK:
        d = read()
        limite = time.time() - SUGGESTION_WINDOW
        d["visites"] = {c: [t for t in ts if t > limite] for c, ts in d["visites"].items() if any(t > limite for t in ts)}
        d["visites"].setdefault(chemin, []).append(int(time.time()))
        d["visites"][chemin] = d["visites"][chemin][-50:]
        write(d)
