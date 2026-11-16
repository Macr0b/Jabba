"""Journal d'activité : ce qui s'est passé sur le serveur, gardé dans /data/journal.jsonl.

Une ligne JSON par événement. On ne garde que les 2000 derniers.
"""

import json
import os
import threading
import time

LOG_FILE = os.environ.get("JABBA_JOURNAL", "/data/journal.jsonl")
# La porte du Wi-Fi (conteneur à part, sans accès aux données de l'appli) note dans son propre fichier.
OTHER_LOGS = [f for f in os.environ.get("JABBA_JOURNAUX_AUTRES", "/data/porte-wifi/journal.jsonl").split(",") if f]
MAX_EVENTS = 2000
_LOCK = threading.Lock()

# Familles d'événements (pour l'icône et le filtre dans l'appli).
FAMILIES = {
    "outil",
    "admin",
    "disque",
    "courant",
    "fichier",
    "appli",
    "alerte",
    "systeme",
    "invite",
    "sauvegardes",
    "nuage",
}


def record(family, text, who=None):
    event = {"t": int(time.time()), "famille": family if family in FAMILIES else "systeme", "texte": text}
    if who:
        event["qui"] = who
    with _LOCK:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")
        try:
            if os.path.getsize(LOG_FILE) > 600_000:
                with open(LOG_FILE) as file:
                    lines = file.readlines()[-MAX_EVENTS:]
                with open(LOG_FILE + ".tmp", "w") as file:
                    file.writelines(lines)
                os.replace(LOG_FILE + ".tmp", LOG_FILE)
        except OSError:
            pass
    return event


def read(count=100, family=None):
    events = []
    for path in [LOG_FILE] + [other_log for other_log in OTHER_LOGS if other_log != LOG_FILE]:
        try:
            with open(path) as file:
                lines = file.readlines()[-MAX_EVENTS:]
        except OSError:
            continue
        for line in lines:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and isinstance(ev.get("t"), int) and isinstance(ev.get("texte"), str):
                if path != LOG_FILE:
                    # Écrit par la porte du Wi-Fi : on ne la croit que pour des événements d'invités.
                    ev = {"t": ev["t"], "famille": "invite", "texte": ev["texte"][:300]}
                events.append(ev)
    events.sort(key=lambda event: event["t"])
    result = []
    for ev in reversed(events):
        if family and ev.get("famille") != family:
            continue
        result.append(ev)
        if len(result) >= count:
            break
    return result
