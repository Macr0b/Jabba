"""Journal d'activité : ce qui s'est passé sur le serveur, gardé dans /data/journal.jsonl.

Une ligne JSON par événement. On ne garde que les 2000 derniers.
"""
import json
import os
import threading
import time

FICHIER = os.environ.get("JABBA_JOURNAL", "/data/journal.jsonl")
MAX = 2000
_VERROU = threading.Lock()

# Familles d'événements (pour l'icône et le filtre dans l'appli).
FAMILLES = {"outil", "admin", "disque", "courant", "fichier", "appli", "alerte", "systeme", "invite"}


def noter(famille, texte, qui=None):
    ev = {"t": int(time.time()), "famille": famille if famille in FAMILLES else "systeme", "texte": texte}
    if qui:
        ev["qui"] = qui
    with _VERROU:
        os.makedirs(os.path.dirname(FICHIER), exist_ok=True)
        with open(FICHIER, "a") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        try:
            if os.path.getsize(FICHIER) > 600_000:
                with open(FICHIER) as f:
                    lignes = f.readlines()[-MAX:]
                with open(FICHIER + ".tmp", "w") as f:
                    f.writelines(lignes)
                os.replace(FICHIER + ".tmp", FICHIER)
        except OSError:
            pass
    return ev


def lire(n=100, famille=None):
    try:
        with open(FICHIER) as f:
            lignes = f.readlines()
    except OSError:
        return []
    res = []
    for ligne in reversed(lignes):
        try:
            ev = json.loads(ligne)
        except ValueError:
            continue
        if famille and ev.get("famille") != famille:
            continue
        res.append(ev)
        if len(res) >= n:
            break
    return res
