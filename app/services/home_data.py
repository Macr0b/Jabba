"""services.home_data"""
import json
import os
import threading
import time



# ---------------------------------------------------------------- accueil : essentiels, favoris, suggestions
DONNEES = os.environ.get("JABBA_DONNEES", "/data/accueil.json")
VERROU = threading.Lock()
SEUIL_SUGGESTION = 5          # ouvertures…
FENETRE_SUGGESTION = 30 * 86400  # …sur 30 jours


def lire_donnees():
    try:
        with open(DONNEES) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("favoris", [])
    d.setdefault("visites", {})
    d.setdefault("ignores", [])
    return d


def ecrire_donnees(d):
    os.makedirs(os.path.dirname(DONNEES), exist_ok=True)
    tmp = DONNEES + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, DONNEES)


def noter_visite(chemin):
    with VERROU:
        d = lire_donnees()
        limite = time.time() - FENETRE_SUGGESTION
        d["visites"] = {c: [t for t in ts if t > limite] for c, ts in d["visites"].items() if any(t > limite for t in ts)}
        d["visites"].setdefault(chemin, []).append(int(time.time()))
        d["visites"][chemin] = d["visites"][chemin][-50:]
        ecrire_donnees(d)
