"""Sauvegardes : quand partir, quoi copier, comment lire le résultat."""
import re
from datetime import datetime

import sauvegardes


def reglages(**k):
    d = {"actif": True, "chemin": "/srv/disques/D", "disque": "D", "heure": "03:30", "lancee": 0,
         "derniere_reussie": None, "sources": {"fichiers": True, "outils": True, "systeme": True}}
    d.update(k)
    return d


def test_quand_partir():
    n = datetime(2026, 11, 10, 4, 0)
    assert sauvegardes.due(reglages(), n)
    assert not sauvegardes.due(reglages(derniere_reussie={"fin": n.timestamp() - 600}), n)
    assert not sauvegardes.due(reglages(lancee=n.timestamp() - 600), n)          # essai il y a 10 min : on attend
    assert sauvegardes.due(reglages(lancee=n.timestamp() - 4000), n)             # plus d'une heure : on réessaie
    avant = datetime(2026, 11, 10, 2, 0)
    assert not sauvegardes.due(reglages(derniere_reussie={"fin": datetime(2026, 11, 9, 3, 40).timestamp()}), avant)
    assert not sauvegardes.due(reglages(actif=False), n)


def test_phrase():
    p = sauvegardes.nouvelle_phrase()
    assert re.fullmatch(r"([A-HJ-NP-Z2-9]{4}-){5}[A-HJ-NP-Z2-9]{4}", p)
    assert not set(p) & set("01OIL")


def test_script_exclut_et_remet_en_lecture_seule():
    s = sauvegardes.script(reglages(), "/opt/stacks/jabba-app/donnees")
    for x in ("/srv/data/downloads", "/srv/data/media", "**/transcodes", "Restaurations"):
        assert x in s
    assert "mount -o remount,ro" in s and "trap fin EXIT" in s
    assert "--keep-daily 7" in s and "--keep-weekly 4" in s and "--keep-monthly 6" in s
    assert "/etc" in s and "/opt/stacks" in s


def test_script_sans_le_systeme():
    s = sauvegardes.script(reglages(sources={"fichiers": True, "outils": True, "systeme": False}), "/d")
    assert " '/etc'" not in s and "/etc " not in s.split("restic backup")[1].split("\n")[0]


def test_resultat():
    assert sauvegardes.resultat({}) is None
    r = sauvegardes.resultat({"code": 0, "debut": 10, "fin": 70, "resume": {"data_added": 5, "snapshot_id": "ab"}})
    assert r["ok"] and r["duree"] == 60 and r["ajoute"] == 5
    r = sauvegardes.resultat({"code": 13, "etape": "init", "debut": 1, "fin": 2})
    assert not r["ok"] and "phrase de passe" in r["erreur"]
