"""Tâches longues lancées depuis l'appli (mise à jour d'un outil…), suivies par l'écran.

Sommaire :
  TASKS : les tâches en cours ou finies (id -> état)
  start_task : lance une action de la liste fermée (host/actions.py) dans un fil à part et note son résultat
"""

import threading
import time
import uuid

import config
from host import commands
from services import activity_log

# ---------------------------------------------------------------- tâches longues
TASKS = {}


def start_task(title, action, args, who=None, tool=None):
    task_id = uuid.uuid4().hex[:8]
    TASKS[task_id] = {
        "id": task_id,
        "titre": title,
        "etat": "en cours",
        "debut": time.time(),
        "journal": "",
        "outil": tool,
    }

    def travail():
        try:
            if config.DEMO:
                time.sleep(3)
                code, output, error_output = 0, "ok", ""
            else:
                code, output, error_output = commands.run_action(action, **args)
            TASKS[task_id]["journal"] = (output + error_output)[-4000:]
            TASKS[task_id]["etat"] = "terminé" if code == 0 else "erreur"
        except Exception as error:  # noqa: BLE001
            TASKS[task_id]["journal"] = str(error)
            TASKS[task_id]["etat"] = "erreur"
        TASKS[task_id]["fin"] = time.time()
        activity_log.record(
            "outil", title + (" : terminé" if TASKS[task_id]["etat"] == "terminé" else " : erreur"), who
        )

    threading.Thread(target=travail, daemon=True).start()
    return TASKS[task_id]
