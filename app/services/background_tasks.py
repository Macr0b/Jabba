"""services.background_tasks"""
import threading
import time
import uuid
from services import activity_log
import config
from host import commands


# ---------------------------------------------------------------- tâches longues
TASKS = {}


def start_task(titre, script, qui=None, outil=None):
    tid = uuid.uuid4().hex[:8]
    TASKS[tid] = {"id": tid, "titre": titre, "etat": "en cours", "debut": time.time(), "journal": "", "outil": outil}

    def travail():
        try:
            if config.DEMO:
                time.sleep(3)
                code, sortie, err = 0, "ok", ""
            else:
                code, sortie, err = commands.run_shell_on_host(script, timeout=1800)
            TASKS[tid]["journal"] = (sortie + err)[-4000:]
            TASKS[tid]["etat"] = "terminé" if code == 0 else "erreur"
        except Exception as e:  # noqa: BLE001
            TASKS[tid]["journal"] = str(e)
            TASKS[tid]["etat"] = "erreur"
        TASKS[tid]["fin"] = time.time()
        activity_log.record("outil", titre + (" : terminé" if TASKS[tid]["etat"] == "terminé" else " : erreur"), qui)

    threading.Thread(target=travail, daemon=True).start()
    return TASKS[tid]
