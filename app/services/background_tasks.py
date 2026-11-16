"""services.background_tasks"""
import threading
import time
import uuid
from services import activity_log
import config
from host import commands


# ---------------------------------------------------------------- tâches longues
TACHES = {}


def lancer_tache(titre, script, qui=None, outil=None):
    tid = uuid.uuid4().hex[:8]
    TACHES[tid] = {"id": tid, "titre": titre, "etat": "en cours", "debut": time.time(), "journal": "", "outil": outil}

    def travail():
        try:
            if config.DEMO:
                time.sleep(3)
                code, sortie, err = 0, "ok", ""
            else:
                code, sortie, err = commands.hote_sh(script, timeout=1800)
            TACHES[tid]["journal"] = (sortie + err)[-4000:]
            TACHES[tid]["etat"] = "terminé" if code == 0 else "erreur"
        except Exception as e:  # noqa: BLE001
            TACHES[tid]["journal"] = str(e)
            TACHES[tid]["etat"] = "erreur"
        TACHES[tid]["fin"] = time.time()
        activity_log.noter("outil", titre + (" : terminé" if TACHES[tid]["etat"] == "terminé" else " : erreur"), qui)

    threading.Thread(target=travail, daemon=True).start()
    return TACHES[tid]
