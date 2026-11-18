"""Mémoire d'atelier (outil de l'appli, à activer dans l'Établi) : le carnet de chaque machine.

Pour chaque machine : ses réglages, son journal (interventions, pannes, entretiens, réglages,
notes, pièces changées), ses rappels d'entretien (tous les N jours) et le dossier de Fichiers où
sont rangés ses manuels et photos. Désactiver l'outil garde tout ; le réactiver le retrouve.

Sommaire :
  is_enabled() / set_enabled(on)         : l'outil est-il activé dans l'Établi
  machines()                             : la liste, avec la dernière intervention et les rappels dus
  machine(id)                            : une machine, son journal et ses rappels
  add_machine / update_machine / delete_machine
  add_entry / delete_entry               : le journal d'une machine
  add_reminder / reminder_done / delete_reminder : les rappels d'entretien
  due_reminders(today)                   : les rappels arrivés à échéance
  watch_reminders()                      : chaque jour, une alerte par rappel arrivé à échéance
"""

import datetime
import os
import threading
import uuid

from services import json_files, notifications

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
STATE_FILE = os.path.join(DATA_DIR, "atelier.json")
ENTRY_TYPES = ("intervention", "panne", "entretien", "reglage", "note")
TEXT_LIMITS = {"nom": 80, "modele": 120, "emplacement": 80, "reglages": 20000, "dossier": 1000}
_lock = threading.Lock()


def _read():
    return json_files.load(STATE_FILE, {"actif": False, "machines": [], "journal": [], "rappels": []})


def _write(state):
    json_files.save(STATE_FILE, state)


def _new_id():
    return uuid.uuid4().hex[:10]


def _today():
    return datetime.date.today().isoformat()


def _text(value, field, required=False):
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"Le champ « {field} » est obligatoire.")
    limit = TEXT_LIMITS.get(field, 5000)
    if len(text) > limit:
        raise ValueError(f"« {field} » : {limit} caractères au plus.")
    return text


def _date(value):
    text = str(value or "").strip() or _today()
    try:
        return datetime.date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError("Date attendue au format AAAA-MM-JJ.")


def _find(items, item_id, what):
    for item in items:
        if item["id"] == item_id:
            return item
    raise KeyError(what)


# ---------------------------------------------------------------- activation
def is_enabled():
    return bool(_read().get("actif"))


def set_enabled(enabled):
    with _lock:
        state = _read()
        state["actif"] = bool(enabled)
        _write(state)


# ---------------------------------------------------------------- rappels
def next_due(reminder):
    last = datetime.date.fromisoformat(reminder["dernier"])
    return (last + datetime.timedelta(days=reminder["tous_les_jours"])).isoformat()


def _reminder_view(reminder, today):
    due = next_due(reminder)
    return {**reminder, "echeance": due, "du": due <= today}


def due_reminders(today=None):
    today = today or _today()
    return [_reminder_view(item, today) for item in _read()["rappels"] if next_due(item) <= today]


# ---------------------------------------------------------------- machines
def machines(today=None):
    today = today or _today()
    state = _read()
    result = []
    for item in sorted(state["machines"], key=lambda machine: machine["nom"].lower()):
        entries = [entry for entry in state["journal"] if entry["machine"] == item["id"]]
        last = max(reversed(entries), key=lambda entry: entry["date"], default=None)
        reminders = [
            _reminder_view(reminder, today) for reminder in state["rappels"] if reminder["machine"] == item["id"]
        ]
        result.append(
            {
                **item,
                "derniere": last,
                "entrees": len(entries),
                "rappels_dus": sum(1 for reminder in reminders if reminder["du"]),
            }
        )
    return result


def machine(machine_id, today=None):
    today = today or _today()
    state = _read()
    item = _find(state["machines"], machine_id, "machine")
    # Le plus récent d'abord ; à date égale, le dernier noté d'abord (ordre d'écriture).
    entries = [entry for entry in state["journal"] if entry["machine"] == machine_id][::-1]
    entries.sort(key=lambda entry: entry["date"], reverse=True)
    reminders = [_reminder_view(reminder, today) for reminder in state["rappels"] if reminder["machine"] == machine_id]
    reminders.sort(key=lambda reminder: reminder["echeance"])
    return {**item, "journal": entries, "rappels": reminders}


def add_machine(fields, who=None):
    item = {
        "id": _new_id(),
        "nom": _text(fields.get("nom"), "nom", required=True),
        "modele": _text(fields.get("modele"), "modele"),
        "emplacement": _text(fields.get("emplacement"), "emplacement"),
        "reglages": _text(fields.get("reglages"), "reglages"),
        "dossier": _text(fields.get("dossier"), "dossier"),
        "cree": _today(),
        "par": who,
    }
    with _lock:
        state = _read()
        if any(machine_item["nom"].lower() == item["nom"].lower() for machine_item in state["machines"]):
            raise ValueError("Une machine porte déjà ce nom.")
        state["machines"].append(item)
        _write(state)
    return item


def update_machine(machine_id, fields):
    with _lock:
        state = _read()
        item = _find(state["machines"], machine_id, "machine")
        for field in ("nom", "modele", "emplacement", "reglages", "dossier"):
            if field in fields:
                item[field] = _text(fields[field], field, required=field == "nom")
        _write(state)
    return item


def delete_machine(machine_id):
    """Retire la machine, son journal et ses rappels (les fichiers de son dossier restent)."""
    with _lock:
        state = _read()
        _find(state["machines"], machine_id, "machine")
        state["machines"] = [item for item in state["machines"] if item["id"] != machine_id]
        state["journal"] = [item for item in state["journal"] if item["machine"] != machine_id]
        state["rappels"] = [item for item in state["rappels"] if item["machine"] != machine_id]
        _write(state)


# ---------------------------------------------------------------- journal
def add_entry(machine_id, fields, who=None):
    kind = fields.get("type") or "intervention"
    if kind not in ENTRY_TYPES:
        raise ValueError("Type inconnu.")
    entry = {
        "id": _new_id(),
        "machine": machine_id,
        "type": kind,
        "date": _date(fields.get("date")),
        "titre": _text(fields.get("titre"), "titre", required=True),
        "texte": _text(fields.get("texte"), "texte"),
        "pieces": _text(fields.get("pieces"), "pieces"),
        "par": who,
        "cree": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with _lock:
        state = _read()
        _find(state["machines"], machine_id, "machine")
        state["journal"].append(entry)
        _write(state)
    return entry


def delete_entry(machine_id, entry_id):
    with _lock:
        state = _read()
        _find([item for item in state["journal"] if item["machine"] == machine_id], entry_id, "entrée")
        state["journal"] = [item for item in state["journal"] if item["id"] != entry_id]
        _write(state)


# ---------------------------------------------------------------- rappels d'entretien
def add_reminder(machine_id, fields):
    try:
        days = int(fields.get("tous_les_jours"))
    except (TypeError, ValueError):
        days = 0
    if not 1 <= days <= 3650:
        raise ValueError("Fréquence : de 1 à 3650 jours.")
    reminder = {
        "id": _new_id(),
        "machine": machine_id,
        "titre": _text(fields.get("titre"), "titre", required=True),
        "tous_les_jours": days,
        "dernier": _date(fields.get("dernier")),
        "alerte": None,
    }
    with _lock:
        state = _read()
        _find(state["machines"], machine_id, "machine")
        state["rappels"].append(reminder)
        _write(state)
    return reminder


def reminder_done(reminder_id, who=None, today=None):
    """L'entretien est fait : le rappel repart d'aujourd'hui, et c'est noté au journal de la machine."""
    today = today or _today()
    with _lock:
        state = _read()
        reminder = _find(state["rappels"], reminder_id, "rappel")
        reminder["dernier"] = today
        reminder["alerte"] = None
        _write(state)
    add_entry(reminder["machine"], {"type": "entretien", "titre": reminder["titre"], "date": today}, who)
    return reminder


def delete_reminder(reminder_id):
    with _lock:
        state = _read()
        _find(state["rappels"], reminder_id, "rappel")
        state["rappels"] = [item for item in state["rappels"] if item["id"] != reminder_id]
        _write(state)


def watch_reminders(today=None):
    """Une seule alerte par échéance (pas tous les jours tant que ce n'est pas fait)."""
    today = today or _today()
    with _lock:
        state = _read()
        if not state.get("actif"):
            return []
        names = {item["id"]: item["nom"] for item in state["machines"]}
        sent = []
        for reminder in state["rappels"]:
            due = next_due(reminder)
            if due <= today and reminder.get("alerte") != due:
                reminder["alerte"] = due
                sent.append(reminder)
        if sent:
            _write(state)
    for reminder in sent:
        notifications.send(
            "atelier",
            f"Entretien à faire : {names.get(reminder['machine'], '?')}",
            reminder["titre"],
            f"/#/atelier?machine={reminder['machine']}",
            family="atelier",
        )
    return sent
