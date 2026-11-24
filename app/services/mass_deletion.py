"""Alerte « suppression massive » : beaucoup d'éléments partis à la corbeille en peu de temps.

Sommaire
- deleted_recently(entries, now) : combien d'éléments de la corbeille datent de la dernière heure.
- watch_mass_deletion()          : appelé par la surveillance toutes les 10 minutes.

Un rançongiciel, une synchronisation qui part de travers ou une fausse manœuvre
se voient de la même façon : des centaines de fichiers supprimés d'un coup.
Tout est encore dans la corbeille : l'alerte laisse le temps de réagir.
"""

import asyncio
import os
import time

from services import notifications, trash

WINDOW_SECONDS = 3600
THRESHOLD = int(os.environ.get("JABBA_ALERTE_SUPPRESSIONS", "200"))
QUIET_SECONDS = 6 * 3600  # une seule alerte par vague
STATE = {"last_alert": 0}


def deleted_recently(entries, now):
    return sum(1 for entry in entries if now - (entry.get("deleted") or 0) <= WINDOW_SECONDS)


def watch_mass_deletion(now=None):
    now = time.time() if now is None else now
    if now - STATE["last_alert"] < QUIET_SECONDS:
        return
    count = deleted_recently(asyncio.run(trash.list_entries()), now)
    if count < THRESHOLD:
        return
    STATE["last_alert"] = now
    notifications.send(
        "securite",
        "Suppression massive",
        f"{count} éléments mis à la corbeille en moins d'une heure. Si ce n'est pas toi, coupe le partage ou le compte concerné : tout est encore dans la corbeille.",
        "/#/corbeille",
        family="securite",
    )
