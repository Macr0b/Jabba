"""Entretien des disques : TRIM des SSD, et fragmentation des disques à plateaux en ext4.

Pourquoi pas une défragmentation automatique de tout :
- un SSD ne doit jamais être défragmenté (ça l'use pour rien) : son entretien, c'est le TRIM,
  qui lui dit quelles zones sont libres (fstrim.timer de Debian, une fois par semaine) ;
- un disque à plateaux en ext4 se fragmente peu : on mesure une fois par mois (e4defrag -c),
  et on ne propose de défragmenter que si le score dépasse 55 (seuil d'e4defrag : « à défragmenter ») ;
- exFAT et NTFS n'ont pas d'outil fiable sous Linux : on ne les touche pas.

Sommaire
- trim_state() / enable_trim()        : le TRIM du SSD.
- spinning_ext4_mounts(lsblk_json)    : les disques à plateaux en ext4, montés (ceux qu'on peut mesurer).
- parse_score(output)                 : le score d'e4defrag (0 à 100).
- level(score)                        : « faible », « moyenne », « forte ».
- measure_all() / defragment(path)    : mesure et défragmentation (longues : lancées à part).
- summary()                           : pour la carte Santé des disques.
- watch_fragmentation()               : une mesure par mois, par la surveillance.
  start_measure : lance la mesure de tous les disques en tâche de fond
  start_defragment : lance la défragmentation d'un disque en tâche de fond
  interne : _read, _write, _partitions, _has_ssd, _start
"""

import json
import os
import re
import threading
import time

import config
from host import actions, commands
from services import activity_log, json_files, notifications

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
STATE_FILE = os.path.join(DATA_DIR, "fragmentation.json")
MEASURE_EVERY_SECONDS = 30 * 86400
NEEDS_DEFRAG = 56  # e4defrag : 0-30 rien à faire, 31-55 un peu fragmenté, 56 et plus : à défragmenter
LITTLE = 31
_lock = threading.Lock()
RUNNING = {"tache": None}  # « mesure » ou « defragmentation : <chemin> » pendant que ça tourne

DEMO = {
    "trim": {"actif": True, "dernier": "lundi dernier", "ssd": True},
    "disques": {"/srv/disques/R2-D2": {"nom": "R2-D2", "score": 12, "lu": 0}},
}


def _read():
    return json_files.load(STATE_FILE, {"disques": {}, "lu": 0})


def _write(state):
    json_files.save(STATE_FILE, state)


# ---------------------------------------------------------------- TRIM
def _partitions():
    code, output, _ = commands.run_action("liste_partitions")
    if code != 0:
        return []
    try:
        return json.loads(output).get("blockdevices", [])
    except ValueError:
        return []


def _has_ssd(devices):
    return any(
        device.get("type") == "disk" and str(device.get("rota")) in ("0", "False", "false") for device in devices
    )


def trim_state(devices=None):
    if config.DEMO:
        return dict(DEMO["trim"])
    devices = _partitions() if devices is None else devices
    code, output, _ = commands.run_action("etat_trim")
    fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line) if code == 0 else {}
    last = fields.get("LastTriggerUSec", "").strip()
    return {
        "ssd": _has_ssd(devices),
        "actif": fields.get("UnitFileState") == "enabled" and fields.get("ActiveState") == "active",
        "dernier": None if last in ("", "n/a", "0") else last,
    }


def enable_trim():
    if config.DEMO:
        return True
    return commands.run_action("activer_trim")[0] == 0


# ---------------------------------------------------------------- fragmentation
def spinning_ext4_mounts(devices):
    """[{nom, chemin}] des partitions ext4 montées sur un disque à plateaux (ROTA = 1)."""
    found = []

    def walk(device, rotational):
        rotational = str(device.get("rota", rotational)) in ("1", "True", "true")
        mount_point = device.get("mountpoint") or ""
        if device.get("fstype") == "ext4" and rotational and mount_point:
            try:
                actions.build("mesurer_fragmentation", {"chemin": mount_point})
            except actions.ActionError:
                pass  # ni sous /srv, /mnt, /media : pas un disque de données
            else:
                found.append({"nom": mount_point.rstrip("/").rsplit("/", 1)[-1], "chemin": mount_point})
        for child in device.get("children") or []:
            walk(child, rotational)

    for device in devices:
        walk(device, device.get("rota"))
    return found


def parse_score(output):
    match = re.search(r"Fragmentation score\s*:?\s*(\d+)", output or "")
    return int(match.group(1)) if match else None


def level(score):
    if score is None:
        return "inconnue"
    if score >= NEEDS_DEFRAG:
        return "forte"
    return "moyenne" if score >= LITTLE else "faible"


def measure_all(now=None):
    now = time.time() if now is None else now
    state = _read()
    previous = state.get("disques", {})
    current = {}
    for disk in spinning_ext4_mounts(_partitions()):
        code, output, _ = commands.run_action("mesurer_fragmentation", chemin=disk["chemin"])
        score = parse_score(output) if code == 0 else None
        current[disk["chemin"]] = {"nom": disk["nom"], "score": score, "lu": int(now)}
        before = (previous.get(disk["chemin"]) or {}).get("score")
        if score is not None and score >= NEEDS_DEFRAG and (before is None or before < NEEDS_DEFRAG):
            notifications.send(
                "sante_disques",
                f"{disk['nom']} est fragmenté",
                f"Score {score}. Disques → Santé des disques → Défragmenter (à lancer quand le disque sert peu).",
                "/#/disques",
                family="disque",
            )
    state = {"disques": current, "lu": int(now)}
    _write(state)
    return state


def defragment(path, by=""):
    code, _, error_output = commands.run_action("defragmenter", chemin=path)
    activity_log.record("disque", f"Défragmentation de {path} " + ("terminée" if code == 0 else "échouée"), by)
    measure_all()
    return code == 0


def _start(label, function):
    """Lance une tâche longue à part ; une seule à la fois."""
    with _lock:
        if RUNNING["tache"]:
            return False
        RUNNING["tache"] = label

    def run():
        try:
            function()
        finally:
            RUNNING["tache"] = None

    threading.Thread(target=run, daemon=True).start()
    return True


def start_measure():
    return _start("mesure", measure_all)


def start_defragment(path, by=""):
    actions.build("defragmenter", {"chemin": path})  # refuse un chemin hors liste avant de lancer
    return _start(f"defragmentation : {path}", lambda: defragment(path, by))


def summary():
    if config.DEMO:
        disks = [{"chemin": path, **value, "niveau": level(value["score"])} for path, value in DEMO["disques"].items()]
        return {"trim": trim_state(), "disques": disks, "lu": None, "en_cours": RUNNING["tache"]}
    devices = _partitions()
    state = _read()
    disks = [
        {"chemin": path, **value, "niveau": level(value.get("score"))}
        for path, value in state.get("disques", {}).items()
    ]
    return {"trim": trim_state(devices), "disques": disks, "lu": state.get("lu") or None, "en_cours": RUNNING["tache"]}


def watch_fragmentation():
    if config.DEMO or time.time() - (_read().get("lu") or 0) < MEASURE_EVERY_SECONDS:
        return
    start_measure()
