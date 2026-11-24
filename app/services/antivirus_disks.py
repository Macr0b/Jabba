"""Antivirus : analyse des disques (en plus des dépôts des invités, services/antivirus.py).

- Nouveaux fichiers du disque de données : chaque heure, les fichiers arrivés ou déplacés depuis
  le dernier passage (envois depuis Fichiers ou Nextcloud, copies…) sont analysés.
- Disques cochés : analyse complète chaque semaine, la nuit (entre 2 h et 6 h), un disque à la fois,
  en tâche de fond sur le serveur et en priorité basse. Le disque de données est coché de base.
- « Analyser maintenant » : la même analyse complète, tout de suite.
Un fichier infecté est mis à l'écart et signalé (antivirus.record_threat).

Sommaire :
  disks(state)            : les disques qu'on peut analyser, cochés ou non, et leur dernière analyse
  set_weekly(path, on)    : cocher / décocher un disque pour l'analyse de la semaine
  start_scan(path)        : lancer l'analyse complète d'un disque (une seule à la fois)
  new_files(folder, since): les fichiers arrivés depuis `since` (date de changement), hors dossiers cachés
  watch()                 : appelé chaque minute : suit l'analyse en cours, lance celle de la semaine,
                            et chaque heure les nouveaux fichiers
  available_disks  : {chemin: nom} : le disque de données et les disques branchés connus de Jabba
  scan_new_files   : les nouveaux fichiers du disque de données (les dépôts des invités ont leur propre suivi)
  interne : _settings, _running, _follow, _weekly_due, _new_files_in_background
"""

import os
import threading
import time

import config
from host import commands
from services import antivirus, files_source

DATA_DISK = "/srv/data"
WEEK = 7 * 24 * 3600
NIGHT_HOURS = range(2, 6)
NEW_FILES_EVERY = 3600
RUNNING = {"new_items": False}


def _settings(state):
    return state.setdefault("disks", {DATA_DISK: {"weekly": True}})


def available_disks():
    """{chemin: nom} : le disque de données et les disques branchés connus de Jabba."""
    found = {DATA_DISK: "Disque de données"}
    for name, path in files_source.known_disks().items():
        if path.startswith("/srv/") and path != DATA_DISK:
            found[path] = name
    return found


def disks(state):
    settings = _settings(state)
    return [
        {
            "path": path,
            "name": name,
            "weekly": bool(settings.get(path, {}).get("weekly")),
            "scan": settings.get(path, {}).get("scan"),
        }
        for path, name in available_disks().items()
    ]


def set_weekly(path, enabled):
    if path not in available_disks():
        raise KeyError(path)
    antivirus.update_state(lambda state: _settings(state).setdefault(path, {}).update(weekly=bool(enabled)))


def _running(state):
    return next(
        (path for path, value in _settings(state).items() if (value.get("scan") or {}).get("state") == "en_cours"),
        None,
    )


def start_scan(path, now=None):
    """Lance l'analyse complète. Faux si une autre tourne déjà."""
    now = time.time() if now is None else now
    if path not in available_disks():
        raise KeyError(path)
    with antivirus.STATE_LOCK:
        state = antivirus.read_state()
        if _running(state):
            return False
        unit = f"jabba-antivirus-{int(now)}"
        if config.DEMO:
            code, error_output = 0, ""
        else:
            code, _, error_output = commands.run_action("analyser_disque", path=path, unit=unit)
        entry = _settings(state).setdefault(path, {})
        if code != 0:
            entry["scan"] = {"state": "erreur", "start": int(now), "end": int(now), "error": error_output[-200:]}
        else:
            entry["scan"] = {"state": "en_cours", "start": int(now), "unit": unit}
        antivirus.write_state(state)
        return code == 0


def _follow(state, now):
    """L'analyse en cours est-elle finie ? Si oui : les fichiers trouvés sont mis à l'écart."""
    path = _running(state)
    if not path:
        return
    if config.DEMO:
        _settings(state)[path]["scan"] = {"state": "fini", "start": int(now), "end": int(now), "threats": 0}
        return
    scan = _settings(state)[path]["scan"]
    _, active, _ = commands.run_action("etat_tache", unit=scan["unit"])
    if active.strip() in ("active", "activating", "reloading"):
        return
    _, log_text, _ = commands.run_action("journal_tache", unit=scan["unit"])
    infected = antivirus.parse_report(log_text)
    for infected_path, threat_name in infected.items():
        if infected_path.startswith(path + "/") and os.path.exists(infected_path):
            antivirus.record_threat(state, infected_path, threat_name, f"trouvé en analysant {path}", now)
    scan.update({"state": "fini", "end": int(now), "threats": len(infected)})
    scan.pop("unit", None)


def _weekly_due(state, now):
    if time.localtime(now).tm_hour not in NIGHT_HOURS or _running(state):
        return None
    for path, value in _settings(state).items():
        last = (value.get("scan") or {}).get("start") or 0
        if value.get("weekly") and path in available_disks() and now - last > WEEK - 12 * 3600:
            return path
    return None


def new_files(folder, since, now, skip=()):
    """Fichiers arrivés ou déplacés depuis `since` (date de changement : une copie qui garde sa
    date d'origine compte aussi), de 100 Mo au plus, finis d'écrire, hors dossiers cachés."""
    found = []
    for root, subfolders, names in os.walk(folder):
        subfolders[:] = [
            name
            for name in subfolders
            if not name.startswith(".")
            and not os.path.islink(os.path.join(root, name))
            and os.path.join(root, name) not in skip
        ]
        for name in names:
            path = os.path.join(root, name)
            try:
                info = os.lstat(path)
            except OSError:
                continue
            changed = max(info.st_ctime, info.st_mtime)
            if (
                os.path.isfile(path)
                and not os.path.islink(path)
                and since < changed <= now - antivirus.SETTLE_SECONDS
                and info.st_size <= antivirus.MAX_ANALYSED_BYTES
            ):
                found.append(path)
    return found


def scan_new_files(now=None):
    """Les nouveaux fichiers du disque de données (les dépôts des invités ont leur propre suivi)."""
    now = time.time() if now is None else now
    since = antivirus.read_state().get("new_since")
    if since is None:  # premier passage : on part de maintenant (l'analyse de la semaine voit le reste)
        return antivirus.update_state(lambda state: state.update(new_since=now))
    pending = new_files(DATA_DISK, since, now, skip=set(antivirus.deposit_folders()))
    infected, done = antivirus.scan_files(pending)

    def apply(state):
        for path, threat_name in infected.items():
            antivirus.record_threat(state, path, threat_name, "arrivé sur le disque de données", now)
        if done:
            state["scans"] = state.get("scans", 0) + len(done)
            state["last_scan"] = int(now)
        if len(done) == len(pending):  # tout est vu ; sinon on garde la date et on réessaiera
            state["new_since"] = now - antivirus.SETTLE_SECONDS

    return antivirus.update_state(apply)


def _new_files_in_background():
    try:
        scan_new_files()
    finally:
        RUNNING["new_items"] = False


def watch(now=None):
    now = time.time() if now is None else now
    if not antivirus.installed():
        return
    with antivirus.STATE_LOCK:
        state = antivirus.update_state(lambda state: _follow(state, now))
        due = _weekly_due(state, now)
    if due:
        start_scan(due, now)
    last = antivirus.read_state().get("new_since") or 0
    if now - last >= NEW_FILES_EVERY and not RUNNING["new_items"]:
        RUNNING["new_items"] = True
        threading.Thread(target=_new_files_in_background, daemon=True).start()
