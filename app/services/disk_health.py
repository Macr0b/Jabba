"""Santé des disques : lecture SMART une fois par jour, alerte au premier signe de fatigue.

Sommaire
- installed()            : smartmontools est-il installé sur le serveur ?
- assess(report)         : verdict d'un rapport « smartctl -j » : (état, raisons, détails).
- check_all()            : lit tous les disques, garde le résultat, alerte si un disque se dégrade.
- summary()              : le dernier résultat, pour l'écran Disques.
- watch_disk_health()    : appelé par la surveillance ; ne relit qu'une fois par 24 h.

On lit avec « -n standby » : un disque endormi n'est jamais réveillé pour l'occasion
(il sera lu au passage suivant). Les états :
- « bon »          : rien à signaler ;
- « a_surveiller » : quelques secteurs remplacés, usure avancée, chaleur ;
- « critique »     : le disque annonce lui-même sa panne prochaine, ou des secteurs illisibles.
"""

import json
import os
import time

import config
from host import commands
from services import notifications

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
STATE_FILE = os.path.join(DATA_DIR, "sante-disques.json")
CHECK_EVERY_SECONDS = 24 * 3600
HOT_CELSIUS = 55
WORN_PERCENT = 90
RANK = {"bon": 0, "inconnu": 0, "a_surveiller": 1, "critique": 2}
LABELS = {"bon": "En bonne santé", "a_surveiller": "À surveiller", "critique": "Critique", "inconnu": "Pas lu"}

# Attributs SMART des disques SATA qui comptent vraiment (identifiant → nom en clair).
ATA_ATTRIBUTES = {
    5: "secteurs remplacés",
    187: "erreurs non corrigées",
    197: "secteurs en attente de remplacement",
    198: "secteurs illisibles",
}


def _read_state():
    try:
        with open(STATE_FILE) as file:
            return json.load(file)
    except (OSError, ValueError):
        return {"disques": {}, "lu": 0}


def _write_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    temporary_file = STATE_FILE + ".tmp"
    with open(temporary_file, "w") as file:
        json.dump(state, file)
    os.replace(temporary_file, STATE_FILE)


def installed():
    if config.DEMO:
        return True
    return commands.run_on_host(["sh", "-c", "command -v smartctl"])[0] == 0


# ---------------------------------------------------------------- verdict
def _ata_counts(report):
    counts = {}
    for attribute in (report.get("ata_smart_attributes") or {}).get("table", []):
        if attribute.get("id") in ATA_ATTRIBUTES:
            counts[attribute["id"]] = int((attribute.get("raw") or {}).get("value") or 0)
    return counts


def assess(report):
    """(état, raisons en clair, détails : température, heures, usure)."""
    reasons = []
    level = "bon"

    def worse(new_level, reason):
        nonlocal level
        reasons.append(reason)
        if RANK[new_level] > RANK[level]:
            level = new_level

    status = report.get("smart_status") or {}
    if status.get("passed") is False:
        worse("critique", "Le disque annonce lui-même une panne prochaine")
    counts = _ata_counts(report)
    for attribute_id, count in counts.items():
        if not count:
            continue
        name = ATA_ATTRIBUTES[attribute_id]
        serious = attribute_id in (197, 198) or count >= 50
        worse("critique" if serious else "a_surveiller", f"{count} {name}")
    nvme = report.get("nvme_smart_health_information_log") or {}
    if nvme.get("critical_warning"):
        worse("critique", "Alerte critique du disque NVMe")
    if nvme.get("media_errors"):
        worse("a_surveiller", f"{nvme['media_errors']} erreurs de lecture")
    used = nvme.get("percentage_used")
    if used is not None and used >= WORN_PERCENT:
        worse("a_surveiller", f"Usé à {used} %")
    temperature = (report.get("temperature") or {}).get("current")
    if temperature is not None and temperature >= HOT_CELSIUS:
        worse("a_surveiller", f"Chaud : {temperature} °C")
    details = {
        "temperature": temperature,
        "heures": (report.get("power_on_time") or {}).get("hours"),
        "usure": used,
        "compteurs": {str(key): value for key, value in counts.items()},
    }
    return level, reasons, details


# ---------------------------------------------------------------- lecture
def _physical_disks():
    code, output, _ = commands.run_on_host(["lsblk", "-J", "-d", "-b", "-o", "NAME,PATH,MODEL,SERIAL,TRAN,TYPE,SIZE"])
    if code != 0:
        return []
    try:
        devices = json.loads(output).get("blockdevices", [])
    except ValueError:
        return []
    return [
        device for device in devices if device.get("type") == "disk" and not device["name"].startswith(("loop", "zram"))
    ]


def _smart_report(path, usb):
    """Le rapport JSON, ou None si le disque dort (ou ne sait pas répondre)."""
    attempts = [["smartctl", "-j", "-H", "-A", "-n", "standby", path]]
    if usb:
        attempts.append(["smartctl", "-j", "-H", "-A", "-n", "standby", "-d", "sat", path])
    for command in attempts:
        code, output, _ = commands.run_on_host(command, timeout=60)
        try:
            report = json.loads(output)
        except ValueError:
            continue
        if code & 2 and "standby" in json.dumps(report.get("smartctl", {})).lower():
            return None  # endormi : on ne le réveille pas
        if (
            report.get("smart_status")
            or report.get("ata_smart_attributes")
            or report.get("nvme_smart_health_information_log")
        ):
            return report
    return None


DEMO_DISKS = {
    "demo-ssd": {
        "nom": "SSD interne /dev/sda",
        "etat": "bon",
        "raisons": [],
        "temperature": 36,
        "heures": 9120,
        "usure": None,
        "compteurs": {},
    },
    "demo-r2": {
        "nom": "R2-D2 /dev/sdb",
        "etat": "a_surveiller",
        "raisons": ["3 secteurs remplacés"],
        "temperature": 41,
        "heures": 21300,
        "usure": None,
        "compteurs": {"5": 3},
    },
}


def check_all(now=None):
    now = time.time() if now is None else now
    if config.DEMO:
        state = {"disques": {key: {**value, "lu": int(now)} for key, value in DEMO_DISKS.items()}, "lu": int(now)}
        _write_state(state)
        return state
    state = _read_state()
    previous = state.get("disques", {})
    current = {}
    for device in _physical_disks():
        key = device.get("serial") or device["path"]
        name = " ".join(part for part in (device.get("model") or "", device["path"]) if part).strip()
        report = _smart_report(device["path"], device.get("tran") == "usb")
        if report is None:
            old = previous.get(key)
            current[key] = (
                {**old, "endormi": True} if old else {"nom": name, "etat": "inconnu", "raisons": [], "lu": None}
            )
            continue
        level, reasons, details = assess(report)
        current[key] = {"nom": name, "etat": level, "raisons": reasons, "lu": int(now), **details}
        before = previous.get(key, {})
        got_worse = RANK[level] > RANK.get(before.get("etat", "bon"), 0)
        counts_grew = (
            any(
                value > int((before.get("compteurs") or {}).get(attribute_id, 0))
                for attribute_id, value in details["compteurs"].items()
            )
            and level != "bon"
        )
        if got_worse or (counts_grew and before):
            notifications.send(
                "sante_disques",
                f"Disque {LABELS[level].lower()} : {name}",
                " · ".join(reasons) + ". Vérifie que la sauvegarde est à jour.",
                "/#/disques",
                family="disque",
            )
    state = {"disques": current, "lu": int(now)}
    _write_state(state)
    return state


def summary():
    state = _read_state()
    return {
        "installe": installed(),
        "lu": state.get("lu") or None,
        "disques": [
            {"id": key, **value, "libelle": LABELS[value["etat"]]} for key, value in state.get("disques", {}).items()
        ],
    }


def watch_disk_health():
    if not installed():
        return
    if time.time() - (_read_state().get("lu") or 0) < CHECK_EVERY_SECONDS:
        return
    check_all()
