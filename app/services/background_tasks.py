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


def start_task(title, action, args, who=None, tool=None, input_text=None, then=None):
    """Lance l'action dans un fil à part. `then(sortie)` : appelé si elle réussit, avec ce que l'action a
    écrit, avant de dire « terminé » (ex. créer l'adresse de l'outil installé) ; le texte qu'il renvoie
    s'ajoute au compte rendu."""
    task_id = uuid.uuid4().hex[:8]
    TASKS[task_id] = {
        "id": task_id,
        "title": title,
        "state": "en cours",
        "start": time.time(),
        "log": "",
        "outil": tool,
    }

    def travail():
        try:
            if config.DEMO:
                time.sleep(3)
                code, output, error_output = 0, "ok", ""
            else:
                code, output, error_output = commands.run_action(action, input_text=input_text, **args)
            TASKS[task_id]["log"] = (output + error_output)[-4000:]
            if code == 0 and then:
                TASKS[task_id]["log"] += "\n" + (then(output) or "")
            TASKS[task_id]["state"] = "terminé" if code == 0 else "erreur"
        except Exception as error:  # noqa: BLE001
            TASKS[task_id]["log"] = str(error)
            TASKS[task_id]["state"] = "erreur"
        TASKS[task_id]["end"] = time.time()
        activity_log.record(
            "outil", title + (" : terminé" if TASKS[task_id]["state"] == "terminé" else " : erreur"), who
        )

    threading.Thread(target=travail, daemon=True).start()
    return TASKS[task_id]
