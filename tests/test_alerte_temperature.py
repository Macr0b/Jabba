"""Alerte de température : seulement si la chaleur dure, une seule fois, puis quand elle retombe."""

import pytest

from services import heat_watch


@pytest.fixture
def alertes(monkeypatch):
    envoyees = []
    monkeypatch.setattr(heat_watch.notifications, "send", lambda type_, titre, *a, **k: envoyees.append((type_, titre)))
    heat_watch.STATE.update(depuis=None, alerte=False)
    return envoyees


def test_un_pic_court_ne_previent_pas(alertes):
    for minute, degres in enumerate([90, 92, 91, 70, 88, 88]):
        assert heat_watch.watch(now=minute * 60, degrees=degres) is None
    assert alertes == []


def test_chaleur_qui_dure_puis_refroidit(alertes):
    resultats = [heat_watch.watch(now=minute * 60, degrees=88) for minute in range(15)]
    assert resultats.count("chaud") == 1 and resultats[10] == "chaud"  # au bout de 10 minutes, une fois
    assert heat_watch.watch(now=16 * 60, degrees=80) is None  # entre 75 et 85 : on attend
    assert heat_watch.watch(now=17 * 60, degrees=60) == "revenu"
    assert heat_watch.watch(now=18 * 60, degrees=55) is None
    assert alertes == [("temperature", "Le serveur chauffe : 88 °C"), ("temperature", "Le serveur a refroidi : 60 °C")]


def test_entre_deux_seuils_le_compteur_continue(alertes):
    """Une minute à 80 °C au milieu d'un long épisode chaud ne remet pas le compteur à zéro."""
    for minute in range(5):
        heat_watch.watch(now=minute * 60, degrees=90)
    heat_watch.watch(now=5 * 60, degrees=80)
    assert heat_watch.watch(now=10 * 60, degrees=90) == "chaud"


def test_sans_sonde_rien(alertes, monkeypatch):
    monkeypatch.setattr(heat_watch.measurements, "temperature", lambda: None)
    assert heat_watch.watch(now=0) is None and alertes == []


def test_le_type_d_alerte_existe():
    assert "temperature" in heat_watch.notifications.TYPES
