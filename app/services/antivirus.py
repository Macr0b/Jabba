"""Antivirus (outil de l'Établi) : les fichiers déposés par les invités sont analysés par ClamAV.

Sommaire
- installed() / install() / remove()  : l'outil (conteneur « antivirus », image officielle ClamAV).
- deposit_folders()                   : les dossiers où les invités peuvent déposer.
- pending_files(state, now)           : les fichiers nouveaux ou modifiés depuis la dernière analyse.
- parse_report(output)                : {chemin: signature} des fichiers infectés.
- scan_pending()                      : analyse, met à l'écart ce qui est infecté, alerte.
- summary()                           : pour la carte de l'Établi.
- watch_deposits()                    : appelé chaque minute ; l'analyse tourne à part.

Un fichier infecté est déplacé dans /srv/data/.quarantaine-antivirus (invisible des invités),
jamais supprimé : l'admin décide. Les fichiers de plus de 100 Mo ne sont pas analysés
(limite par défaut de clamd) ; ce sont presque toujours des vidéos.
"""

import os
import re
import shutil
import threading
import time

import config
from host import actions, commands
from services import activity_log, guests, json_files, notifications

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
STATE_FILE = os.path.join(DATA_DIR, "antivirus.json")
QUARANTINE = os.environ.get("JABBA_QUARANTAINE", "/srv/data/.quarantaine-antivirus")
SETTLE_SECONDS = 30  # un fichier encore en cours d'envoi n'est pas analysé
MAX_ANALYSED_BYTES = 100 * 1024**2
FOUND = re.compile(r"^(.*): (.+) FOUND$")
_lock = threading.Lock()
RUNNING = {"analyse": False, "installation": False}


def _read():
    return json_files.load(STATE_FILE, {"vus": {}, "derniere_analyse": None, "analyses": 0, "menaces": []})


def _write(state):
    json_files.save(STATE_FILE, state)


# ---------------------------------------------------------------- l'outil
def installed():
    if config.DEMO:
        return True
    code, output, _ = commands.run_action("conteneur_actif", nom="antivirus")
    return code == 0 and output.strip() == "true"


def _install():
    try:
        code, _, error_output = commands.run_action("installer_antivirus")
        if code == 0:
            activity_log.record("outil", "Antivirus installé")
        else:
            notifications.send(
                "outil_panne",
                "Installation de l'antivirus impossible",
                error_output[-200:],
                "/#/outils",
                family="outil",
            )
    finally:
        RUNNING["installation"] = False


def install():
    """Lance l'installation à part (téléchargement d'environ 300 Mo). Faux si elle tourne déjà."""
    with _lock:
        if RUNNING["installation"]:
            return False
        RUNNING["installation"] = True
    threading.Thread(target=_install, daemon=True).start()
    return True


def remove():
    return commands.run_action("retirer_antivirus")[0] == 0


# ---------------------------------------------------------------- analyse
def deposit_folders():
    return [folder["chemin"] for folder in guests.read().get("dossiers", []) if folder.get("mode") == "depot"]


def pending_files(state, now):
    found = []
    seen = state.setdefault("vus", {})
    for folder in deposit_folders():
        for root, subfolders, names in os.walk(folder):
            subfolders[:] = [
                name for name in subfolders if not name.startswith(".") and not os.path.islink(os.path.join(root, name))
            ]
            for name in names:
                path = os.path.join(root, name)
                try:
                    info = os.lstat(path)
                except OSError:
                    continue
                if not os.path.isfile(path) or os.path.islink(path) or now - info.st_mtime < SETTLE_SECONDS:
                    continue
                signature = [info.st_size, int(info.st_mtime)]
                if seen.get(path) == signature or info.st_size > MAX_ANALYSED_BYTES:
                    continue
                found.append((path, signature))
    return found


def parse_report(output):
    infected = {}
    for line in (output or "").splitlines():
        match = FOUND.match(line.strip())
        if match:
            infected[match.group(1)] = match.group(2)
    return infected


def _quarantine(path):
    os.makedirs(QUARANTINE, mode=0o700, exist_ok=True)
    target = os.path.join(QUARANTINE, time.strftime("%Y%m%d-%H%M%S-") + os.path.basename(path))
    shutil.move(path, target)
    return target


def scan_pending(now=None):
    now = time.time() if now is None else now
    state = _read()
    pending = pending_files(state, now)
    for start in range(0, len(pending), actions.MAX_SCAN_FILES):
        batch = pending[start : start + actions.MAX_SCAN_FILES]
        code, output, _ = commands.run_action("analyser_fichiers", chemins=[path for path, _ in batch])
        if code not in (0, 1):
            break  # antivirus pas prêt (signatures en cours de chargement) : on réessaiera
        infected = parse_report(output)
        for path, signature in batch:
            if path in infected:
                moved = _quarantine(path)
                threat = {
                    "fichier": os.path.basename(path),
                    "origine": path,
                    "menace": infected[path],
                    "date": int(now),
                    "ecart": moved,
                }
                state["menaces"] = ([threat] + state.get("menaces", []))[:50]
                activity_log.record("securite", f"Antivirus : {infected[path]} dans {path}, mis à l'écart")
                notifications.send(
                    "securite",
                    "Fichier infecté mis à l'écart",
                    f"« {os.path.basename(path)} » ({infected[path]}), déposé par un invité. Il est isolé, rien n'est supprimé.",
                    "/#/outils",
                    family="securite",
                )
            else:
                state["vus"][path] = signature
        state["analyses"] = state.get("analyses", 0) + len(batch)
        state["derniere_analyse"] = int(now)
    state["vus"] = {path: value for path, value in state.get("vus", {}).items() if os.path.exists(path)}
    _write(state)
    return state


def _scan_in_background():
    try:
        scan_pending()
    finally:
        RUNNING["analyse"] = False


def watch_deposits():
    if RUNNING["analyse"] or not installed():
        return
    RUNNING["analyse"] = True
    threading.Thread(target=_scan_in_background, daemon=True).start()


def summary():
    state = _read()
    return {
        "installe": installed(),
        "installation": RUNNING["installation"],
        "derniere_analyse": state.get("derniere_analyse"),
        "analyses": state.get("analyses", 0),
        "menaces": state.get("menaces", [])[:10],
    }
