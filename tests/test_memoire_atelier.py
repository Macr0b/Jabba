"""Mémoire d'atelier : machines, journal, rappels d'entretien et leurs alertes."""

import pytest

from services import optional_tools, workshop


@pytest.fixture
def atelier(tmp_path, monkeypatch):
    monkeypatch.setattr(workshop, "STATE_FILE", str(tmp_path / "atelier.json"))
    alertes = []
    monkeypatch.setattr(workshop.notifications, "send", lambda *a, **k: alertes.append(a))
    workshop.set_enabled(True)
    return alertes


def test_activer_dans_l_etabli_garde_les_donnees(atelier):
    laser = workshop.add_machine({"nom": "Laser Trotec", "modele": "Speedy 360"})
    assert optional_tools.remove("atelier") is None and not workshop.is_enabled()
    assert optional_tools.install("atelier") is None and workshop.is_enabled()
    assert workshop.machines()[0]["id"] == laser["id"]  # réactivé : tout est retrouvé
    integre = [outil for outil in optional_tools.catalog(set()) if outil.get("integre")]
    assert integre[0]["id"] == "atelier" and integre[0]["installe"] and integre[0]["page"] == "#/atelier"


def test_machine_et_journal(atelier):
    cnc = workshop.add_machine({"nom": "CNC 3x2", "emplacement": "Fablab"}, "rob")
    with pytest.raises(ValueError):
        workshop.add_machine({"nom": "cnc 3X2"})  # même nom
    with pytest.raises(ValueError):
        workshop.add_machine({"nom": "  "})
    workshop.add_entry(cnc["id"], {"type": "panne", "date": "2026-11-02", "titre": "Broche bloquée"}, "rob")
    workshop.add_entry(
        cnc["id"],
        {"type": "intervention", "date": "2026-11-10", "titre": "Roulements changés", "pieces": "2 × 6204"},
        "rob",
    )
    liste = workshop.machines()
    assert liste[0]["derniere"]["titre"] == "Roulements changés" and liste[0]["entrees"] == 2
    detail = workshop.machine(cnc["id"])
    assert [entree["titre"] for entree in detail["journal"]] == ["Roulements changés", "Broche bloquée"]
    for champs in ({"type": "inconnu", "titre": "x"}, {"titre": ""}, {"titre": "x", "date": "demain"}):
        with pytest.raises(ValueError):
            workshop.add_entry(cnc["id"], champs)
    with pytest.raises(KeyError):
        workshop.add_entry("inconnue", {"titre": "x"})
    workshop.update_machine(cnc["id"], {"reglages": "Fraise 6 mm : 18 000 tr/min", "dossier": "/Atelier/CNC"})
    assert workshop.machine(cnc["id"])["dossier"] == "/Atelier/CNC"
    workshop.delete_entry(cnc["id"], detail["journal"][1]["id"])
    workshop.delete_machine(cnc["id"])
    assert workshop.machines() == [] and workshop._read()["journal"] == []


def test_rappels_d_entretien(atelier):
    laser = workshop.add_machine({"nom": "Laser"})
    rappel = workshop.add_reminder(
        laser["id"], {"titre": "Nettoyer la lentille", "tous_les_jours": 30, "dernier": "2026-10-01"}
    )
    for jours in (0, -3, "x", 4000):
        with pytest.raises(ValueError):
            workshop.add_reminder(laser["id"], {"titre": "x", "tous_les_jours": jours})
    assert workshop.due_reminders("2026-10-30") == []
    assert [item["id"] for item in workshop.due_reminders("2026-10-31")] == [rappel["id"]]
    assert workshop.machines("2026-11-05")[0]["rappels_dus"] == 1
    # une seule alerte par échéance, pas une par jour
    assert len(workshop.watch_reminders("2026-11-01")) == 1
    assert workshop.watch_reminders("2026-11-02") == []
    assert atelier[0][1] == "Entretien à faire : Laser" and atelier[0][3] == f"/#/atelier?machine={laser['id']}"
    workshop.reminder_done(rappel["id"], "rob", today="2026-11-03")
    detail = workshop.machine(laser["id"], "2026-11-03")
    assert detail["rappels"][0]["echeance"] == "2026-12-03" and not detail["rappels"][0]["du"]
    assert detail["journal"][0]["type"] == "entretien" and detail["journal"][0]["titre"] == "Nettoyer la lentille"
    assert len(workshop.watch_reminders("2026-12-03")) == 1  # la nouvelle échéance a son alerte


def test_rien_quand_l_outil_est_desactive(atelier):
    laser = workshop.add_machine({"nom": "Laser"})
    workshop.add_reminder(laser["id"], {"titre": "x", "tous_les_jours": 1, "dernier": "2026-01-01"})
    workshop.set_enabled(False)
    assert workshop.watch_reminders("2026-11-01") == []


def test_a_date_egale_le_dernier_note_d_abord(atelier):
    laser = workshop.add_machine({"nom": "Laser"})
    for titre in ("premier", "deuxième", "troisième"):
        workshop.add_entry(laser["id"], {"titre": titre, "date": "2026-11-18"})
    assert [entree["titre"] for entree in workshop.machine(laser["id"])["journal"]] == [
        "troisième",
        "deuxième",
        "premier",
    ]
    assert workshop.machines()[0]["derniere"]["titre"] == "troisième"
