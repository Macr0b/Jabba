"""Mémoire d'atelier : machines, journal, rappels d'entretien et leurs alertes."""

import pytest

from services import optional_tools, workshop


@pytest.fixture
def workshop_data(tmp_path, monkeypatch):
    monkeypatch.setattr(workshop, "STATE_FILE", str(tmp_path / "atelier.json"))
    alerts = []
    monkeypatch.setattr(workshop.notifications, "send", lambda *a, **k: alerts.append(a))
    workshop.set_enabled(True)
    return alerts


def test_enabling_in_the_workbench_keeps_the_data(workshop_data):
    laser = workshop.add_machine({"name": "Laser Trotec", "model": "Speedy 360"})
    assert optional_tools.remove("atelier") is None and not workshop.is_enabled()
    assert optional_tools.install("atelier") is None and workshop.is_enabled()
    assert workshop.machines()[0]["id"] == laser["id"]  # réactivé : tout est retrouvé
    built_in = [outil for outil in optional_tools.catalog(set()) if outil.get("built_in")]
    assert built_in[0]["id"] == "atelier" and built_in[0]["installed"] and built_in[0]["page"] == "#/atelier"


def test_machine_and_log(workshop_data):
    cnc = workshop.add_machine({"name": "CNC 3x2", "location": "Fablab"}, "rob")
    with pytest.raises(ValueError):
        workshop.add_machine({"name": "cnc 3X2"})  # même nom
    with pytest.raises(ValueError):
        workshop.add_machine({"name": "  "})
    workshop.add_entry(cnc["id"], {"type": "panne", "date": "2026-11-02", "title": "Broche bloquée"}, "rob")
    workshop.add_entry(
        cnc["id"],
        {"type": "intervention", "date": "2026-11-10", "title": "Roulements changés", "parts": "2 × 6204"},
        "rob",
    )
    listing = workshop.machines()
    assert listing[0]["latest"]["title"] == "Roulements changés" and listing[0]["entries"] == 2
    detail = workshop.machine(cnc["id"])
    assert [entry["title"] for entry in detail["log"]] == ["Roulements changés", "Broche bloquée"]
    for fields in ({"type": "inconnu", "title": "x"}, {"title": ""}, {"title": "x", "date": "demain"}):
        with pytest.raises(ValueError):
            workshop.add_entry(cnc["id"], fields)
    with pytest.raises(KeyError):
        workshop.add_entry("inconnue", {"title": "x"})
    workshop.update_machine(cnc["id"], {"settings": "Fraise 6 mm : 18 000 tr/min", "folder": "/Atelier/CNC"})
    assert workshop.machine(cnc["id"])["folder"] == "/Atelier/CNC"
    workshop.delete_entry(cnc["id"], detail["log"][1]["id"])
    workshop.delete_machine(cnc["id"])
    assert workshop.machines() == [] and workshop._read()["log"] == []


def test_maintenance_reminders(workshop_data):
    laser = workshop.add_machine({"name": "Laser"})
    reminder = workshop.add_reminder(
        laser["id"], {"title": "Nettoyer la lentille", "every_days": 30, "last": "2026-10-01"}
    )
    for days in (0, -3, "x", 4000):
        with pytest.raises(ValueError):
            workshop.add_reminder(laser["id"], {"title": "x", "every_days": days})
    assert workshop.due_reminders("2026-10-30") == []
    assert [item["id"] for item in workshop.due_reminders("2026-10-31")] == [reminder["id"]]
    assert workshop.machines("2026-11-05")[0]["reminders_due"] == 1
    # une seule alerte par échéance, pas une par jour
    assert len(workshop.watch_reminders("2026-11-01")) == 1
    assert workshop.watch_reminders("2026-11-02") == []
    assert (
        workshop_data[0][1] == "Entretien à faire : Laser"
        and workshop_data[0][3] == f"/#/atelier?machine={laser['id']}"
    )
    workshop.reminder_done(reminder["id"], "rob", today="2026-11-03")
    detail = workshop.machine(laser["id"], "2026-11-03")
    assert detail["reminders"][0]["due_date"] == "2026-12-03" and not detail["reminders"][0]["due"]
    assert detail["log"][0]["type"] == "entretien" and detail["log"][0]["title"] == "Nettoyer la lentille"
    assert len(workshop.watch_reminders("2026-12-03")) == 1  # la nouvelle échéance a son alerte


def test_nothing_when_the_tool_is_off(workshop_data):
    laser = workshop.add_machine({"name": "Laser"})
    workshop.add_reminder(laser["id"], {"title": "x", "every_days": 1, "last": "2026-01-01"})
    workshop.set_enabled(False)
    assert workshop.watch_reminders("2026-11-01") == []


def test_same_date_latest_entry_first(workshop_data):
    laser = workshop.add_machine({"name": "Laser"})
    for title in ("premier", "deuxième", "troisième"):
        workshop.add_entry(laser["id"], {"title": title, "date": "2026-11-18"})
    assert [entry["title"] for entry in workshop.machine(laser["id"])["log"]] == [
        "troisième",
        "deuxième",
        "premier",
    ]
    assert workshop.machines()[0]["latest"]["title"] == "troisième"


def test_fix_an_entry(workshop_data):
    laser = workshop.add_machine({"name": "Laser"})
    note = workshop.add_entry(
        laser["id"], {"type": "note", "date": "2026-11-02", "title": "Lentile sale", "text": "x"}, "rob"
    )
    fixed = workshop.update_entry(
        laser["id"], note["id"], {"title": "Lentille sale", "type": "entretien", "parts": 'Lentille 2"'}
    )
    assert fixed["title"] == "Lentille sale" and fixed["type"] == "entretien" and fixed["parts"] == 'Lentille 2"'
    assert fixed["text"] == "x" and fixed["date"] == "2026-11-02"  # les champs non envoyés ne bougent pas
    assert fixed["by"] == "rob" and fixed["modified"]
    assert workshop.machine(laser["id"])["log"][0]["title"] == "Lentille sale"  # bien enregistré
    for fields in ({"title": ""}, {"type": "inconnu"}, {"date": "demain"}, {"title": "x" * 201}):
        with pytest.raises(ValueError):
            workshop.update_entry(laser["id"], note["id"], fields)
    other = workshop.add_machine({"name": "CNC"})
    with pytest.raises(KeyError):
        workshop.update_entry(other["id"], note["id"], {"title": "y"})  # la note d'une autre machine
    with pytest.raises(KeyError):
        workshop.update_entry(laser["id"], "inconnue", {"title": "y"})
    assert workshop.machine(laser["id"])["log"][0]["title"] == "Lentille sale"  # rien d'abîmé par les refus
