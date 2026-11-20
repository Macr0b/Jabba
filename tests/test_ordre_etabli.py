"""L'ordre des cartes de l'Établi : enregistré tel quel, sans doublon, et rien d'autre qu'une liste d'outils."""

import pytest

from services import card_order


@pytest.fixture(autouse=True)
def fichier(tmp_path, monkeypatch):
    monkeypatch.setattr(card_order, "ORDER_FILE", str(tmp_path / "ordre.json"))


def test_ordre_vide_au_depart():
    assert card_order.read() == []


def test_ordre_garde_sans_doublon():
    assert card_order.save(["jellyfin", "adguard-home", "jellyfin", "atelier"]) == [
        "jellyfin",
        "adguard-home",
        "atelier",
    ]
    assert card_order.read() == ["jellyfin", "adguard-home", "atelier"]


@pytest.mark.parametrize(
    "ordre", [None, "jellyfin", [1], ["../etc"], ["A-Majuscule"], ["x" * 41], ["ok"] * 101, [{"id": "x"}]]
)
def test_ordre_refuse(ordre):
    with pytest.raises(ValueError):
        card_order.save(ordre)
    assert card_order.read() == []
