"""Alerte de température : seulement si la chaleur dure, une seule fois, puis quand elle retombe."""

import pytest

from services import heat_watch


@pytest.fixture
def alerts(monkeypatch):
    sent_items = []
    monkeypatch.setattr(
        heat_watch.notifications, "send", lambda type_, title, *a, **k: sent_items.append((type_, title))
    )
    heat_watch.STATE.update(since=None, alerte=False)
    return sent_items


def test_a_short_spike_does_not_alert(alerts):
    for minute, degrees_ in enumerate([90, 92, 91, 70, 88, 88]):
        assert heat_watch.watch(now=minute * 60, degrees=degrees_) is None
    assert alerts == []


def test_lasting_heat_then_cooling(alerts):
    results = [heat_watch.watch(now=minute * 60, degrees=88) for minute in range(15)]
    assert results.count("chaud") == 1 and results[10] == "chaud"  # au bout de 10 minutes, une fois
    assert heat_watch.watch(now=16 * 60, degrees=80) is None  # entre 75 et 85 : on attend
    assert heat_watch.watch(now=17 * 60, degrees=60) == "revenu"
    assert heat_watch.watch(now=18 * 60, degrees=55) is None
    assert alerts == [("temperature", "Le serveur chauffe : 88 °C"), ("temperature", "Le serveur a refroidi : 60 °C")]


def test_between_thresholds_the_counter_continues(alerts):
    """Une minute à 80 °C au milieu d'un long épisode chaud ne remet pas le compteur à zéro."""
    for minute in range(5):
        heat_watch.watch(now=minute * 60, degrees=90)
    heat_watch.watch(now=5 * 60, degrees=80)
    assert heat_watch.watch(now=10 * 60, degrees=90) == "chaud"


def test_no_sensor_nothing(alerts, monkeypatch):
    monkeypatch.setattr(heat_watch.measurements, "temperature", lambda: None)
    assert heat_watch.watch(now=0) is None and alerts == []


def test_the_alert_type_exists():
    assert "temperature" in heat_watch.notifications.TYPES
