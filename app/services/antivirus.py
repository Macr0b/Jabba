"""Antivirus (outil de l'Établi) : les fichiers déposés par les invités sont analysés par ClamAV.
L'analyse des disques (nouveaux fichiers, chaque semaine, à la demande) : services/antivirus_disks.py.

Sommaire
- installed() / install() / remove()  : l'outil (conteneur « antivirus », image officielle ClamAV).
- install_by_default()                : installé de base une fois, si le serveur a au moins 4 Go de mémoire.
- record_threat(...)                  : un fichier infecté : mis à l'écart, noté, alerte.
- deposit_folders()                   : les dossiers où les invités peuvent déposer.
- pending_files(state, now)           : les fichiers nouveaux ou modifiés depuis la dernière analyse.
- parse_report(output)                : {chemin: signature} des fichiers infectés.
- scan_pending()                      : analyse, met à l'écart ce qui est infecté, alerte.
- summary()                           : pour la carte de l'Établi.
- watch_deposits()                    : appelé chaque minute ; l'analyse tourne à part.

Un fichier infecté est déplacé dans /srv/data/.quarantaine-antivirus (invisible des invités),
jamais supprimé : l'admin décide. Les fichiers de plus de 100 Mo ne sont pas analysés
(limite par défaut de clamd) ; ce sont presque toujours des vidéos.
  read_state    : l'état de l'antivirus (dernier passage, menaces)
  write_state   : enregistre l'état de l'antivirus
  scan_files    : analyse par lots. -> (infectés {chemin: menace}, chemins analysés) ; s'arrête si l'antivirus n'est pas prêt
  update_state  : lire, modifier, écrire l'état, sans perdre ce qu'une autre analyse écrit en même temps
  interne : _read, _write, _quarantine, _scan_in_background
"""

import os
import re
import shutil
import threading
import time

import config
from host import actions_tools, commands
from services import activity_log, guests, json_files, measurements, notifications

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
STATE_FILE = os.path.join(DATA_DIR, "antivirus.json")
QUARANTINE = os.environ.get("JABBA_QUARANTAINE", "/srv/data/.quarantaine-antivirus")
SETTLE_SECONDS = 30  # un fichier encore en cours d'envoi n'est pas analysé
MAX_ANALYSED_BYTES = 100 * 1024**2
MEMORY_NEEDED = 4 * 1000**3 * 0.95  # « 4 Go » : une machine de 4 Go en annonce un peu moins
FOUND = re.compile(r"^(.*): (.+) FOUND$")
_lock = threading.Lock()
STATE_LOCK = threading.RLock()
RUNNING = {"analyse": False, "installation": False}


def _read():
    return json_files.load(STATE_FILE, {"vus": {}, "derniere_analyse": None, "analyses": 0, "menaces": []})


def read_state():
    return _read()


def write_state(state):
    with STATE_LOCK:
        _write(state)


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
    removed = commands.run_action("retirer_antivirus")[0] == 0
    if removed:
        state = _read()
        state["retire"] = True  # retiré par un admin : jamais réinstallé tout seul
        _write(state)
    return removed


def install_by_default(total_memory=None):
    """Installé de base, une seule fois, si le serveur a la mémoire (environ 1 Go pour ClamAV).
    Ensuite, c'est l'admin qui décide (Établi : Installer / Retirer)."""
    state = _read()
    if state.get("de_base") or config.DEMO:
        return state.get("de_base")
    if total_memory is None:
        total_memory = measurements.memory()["total"]
    if total_memory < MEMORY_NEEDED:
        state["de_base"] = "memoire_insuffisante"
    elif state.get("retire") or installed():
        state["de_base"] = "deja_decide"
    else:
        state["de_base"] = "installe"
        install()
        activity_log.record("outil", "Antivirus installé de base (mémoire suffisante)")
    _write(state)
    return state["de_base"]


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


def record_threat(state, path, threat_name, origin, now):
    """Un fichier infecté : mis à l'écart si possible (jamais supprimé), noté, alerte envoyée."""
    try:
        moved = _quarantine(path)
        where = "Il est isolé, rien n'est supprimé."
    except OSError:
        moved = None  # disque en lecture seule : on ne peut pas le déplacer, on prévient
        where = "Il n'a pas pu être déplacé (disque en lecture seule ?) : à supprimer ou isoler à la main."
    threat = {
        "fichier": os.path.basename(path),
        "origine": path,
        "menace": threat_name,
        "date": int(now),
        "ecart": moved,
    }
    state["menaces"] = ([threat] + state.get("menaces", []))[:50]
    activity_log.record("securite", f"Antivirus : {threat_name} dans {path}" + (", mis à l'écart" if moved else ""))
    notifications.send(
        "securite",
        "Fichier infecté trouvé" if moved is None else "Fichier infecté mis à l'écart",
        f"« {os.path.basename(path)} » ({threat_name}), {origin}. {where}",
        "/#/outils",
        family="securite",
    )


def scan_files(paths):
    """Analyse par lots. -> (infectés {chemin: menace}, chemins analysés) ; s'arrête si l'antivirus n'est pas prêt."""
    infected, done = {}, []
    for start in range(0, len(paths), actions_tools.MAX_SCAN_FILES):
        batch = paths[start : start + actions_tools.MAX_SCAN_FILES]
        code, output, _ = commands.run_action("analyser_fichiers", chemins=batch)
        if code not in (0, 1):
            break  # antivirus pas prêt (signatures en cours de chargement) : on réessaiera
        infected.update(parse_report(output))
        done += batch
    return infected, done


def update_state(change):
    """Lire, modifier, écrire l'état, sans perdre ce qu'une autre analyse écrit en même temps."""
    with STATE_LOCK:
        state = _read()
        change(state)
        _write(state)
        return state


def scan_pending(now=None):
    now = time.time() if now is None else now
    pending = pending_files(_read(), now)
    signatures = dict(pending)
    infected, done = scan_files([path for path, _ in pending])

    def apply(state):
        for path in done:
            if path in infected:
                record_threat(state, path, infected[path], "déposé par un invité", now)
            else:
                state.setdefault("vus", {})[path] = signatures[path]
        if done:
            state["analyses"] = state.get("analyses", 0) + len(done)
            state["derniere_analyse"] = int(now)
        state["vus"] = {path: value for path, value in state.get("vus", {}).items() if os.path.exists(path)}

    return update_state(apply)


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
        "de_base": state.get("de_base"),
    }
