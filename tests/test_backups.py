"""Sauvegardes : quand partir, quoi copier, comment lire le résultat."""

import re
from datetime import datetime

from services import backups


def settings(**k):
    d = {
        "actif": True,
        "chemin": "/srv/disques/D",
        "disque": "D",
        "heure": "03:30",
        "lancee": 0,
        "derniere_reussie": None,
        "sources": {"fichiers": True, "outils": True, "systeme": True},
    }
    d.update(k)
    return d


def test_when_to_start():
    n = datetime(2026, 11, 10, 4, 0)
    assert backups.is_due(settings(), n)
    assert not backups.is_due(settings(derniere_reussie={"fin": n.timestamp() - 600}), n)
    assert not backups.is_due(settings(lancee=n.timestamp() - 600), n)  # essai il y a 10 min : on attend
    assert backups.is_due(settings(lancee=n.timestamp() - 4000), n)  # plus d'une heure : on réessaie
    before = datetime(2026, 11, 10, 2, 0)
    assert not backups.is_due(settings(derniere_reussie={"fin": datetime(2026, 11, 9, 3, 40).timestamp()}), before)
    assert not backups.is_due(settings(actif=False), n)


def test_passphrase():
    p = backups.new_passphrase()
    assert re.fullmatch(r"([A-HJ-NP-Z2-9]{4}-){5}[A-HJ-NP-Z2-9]{4}", p)
    assert not set(p) & set("01OIL")


def test_script_excludes_and_restores_read_only():
    s = backups.backup_script(settings(), "/opt/stacks/jabba-app/donnees")
    for x in ("/srv/data/downloads", "/srv/data/media", "**/transcodes", "Restaurations"):
        assert x in s
    assert "mount -o remount,ro" in s and "trap finish EXIT" in s
    assert "\nkeep_daily=7\nkeep_weekly=4\nkeep_monthly=6\n" in s
    assert "/etc" in s and "/opt/stacks" in s


def test_script_without_the_system():
    s = backups.backup_script(settings(sources={"fichiers": True, "outils": True, "systeme": False}), "/d")
    assert " '/etc'" not in s and "/etc " not in s.split("restic backup")[1].split("\n")[0]


def test_result():
    assert backups.result({}) is None
    r = backups.result({"code": 0, "debut": 10, "fin": 70, "resume": {"data_added": 5, "snapshot_id": "ab"}})
    assert r["ok"] and r["duree"] == 60 and r["ajoute"] == 5
    r = backups.result({"code": 13, "etape": "init", "debut": 1, "fin": 2})
    assert not r["ok"] and "phrase de passe" in r["erreur"]
