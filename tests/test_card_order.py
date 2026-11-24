"""Le rangement des cartes : ordre et cartes masquées, par écran ; rien d'autre qu'une liste d'identifiants."""

import json

import pytest

from services import card_order


@pytest.fixture(autouse=True)
def fichiers(tmp_path, monkeypatch):
    monkeypatch.setattr(card_order, "ORDER_FILE", str(tmp_path / "rangement.json"))
    monkeypatch.setattr(card_order, "OLD_FILE", str(tmp_path / "ordre-etabli.json"))
    return tmp_path


def test_empty_at_start():
    for ecran in card_order.SCREENS:
        assert card_order.read(ecran) == {"ordre": [], "masques": []}


def test_order_and_hidden_cards_per_screen():
    card_order.save("etabli", ["jellyfin", "adguard-home", "jellyfin"], ["immich"])
    card_order.save("accueil-tuiles", ["/Mes dossiers/Atelier", "/R2-D2"], [])
    assert card_order.read("etabli") == {"ordre": ["jellyfin", "adguard-home"], "masques": ["immich"]}
    assert card_order.read("accueil-tuiles")["ordre"] == ["/Mes dossiers/Atelier", "/R2-D2"]
    assert card_order.read("accueil-mesures") == {"ordre": [], "masques": []}  # un écran ne touche pas les autres


def test_old_file_taken_over(fichiers):
    (fichiers / "ordre-etabli.json").write_text(json.dumps({"ordre": ["atelier", "jellyfin"]}))
    assert card_order.read("etabli") == {"ordre": ["atelier", "jellyfin"], "masques": []}


@pytest.mark.parametrize("order", [None, "jellyfin", [1], [""], ["a\nb"], ["x" * 301], ["ok"] * 201, [{"id": "x"}]])
def test_order_refused(order):
    with pytest.raises(ValueError):
        card_order.save("etabli", order)
    assert card_order.read("etabli") == {"ordre": [], "masques": []}


def test_unknown_screen():
    with pytest.raises(ValueError):
        card_order.read("../etc")
    with pytest.raises(ValueError):
        card_order.save("systeme", [])
