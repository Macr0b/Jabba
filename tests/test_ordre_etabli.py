"""Le rangement des cartes : ordre et cartes masquées, par écran ; rien d'autre qu'une liste d'identifiants."""

import json

import pytest

from services import card_order


@pytest.fixture(autouse=True)
def fichiers(tmp_path, monkeypatch):
    monkeypatch.setattr(card_order, "ORDER_FILE", str(tmp_path / "rangement.json"))
    monkeypatch.setattr(card_order, "OLD_FILE", str(tmp_path / "ordre-etabli.json"))
    return tmp_path


def test_vide_au_depart():
    for ecran in card_order.SCREENS:
        assert card_order.read(ecran) == {"ordre": [], "masques": []}


def test_ordre_et_masques_par_ecran():
    card_order.save("etabli", ["jellyfin", "adguard-home", "jellyfin"], ["immich"])
    card_order.save("accueil-tuiles", ["/Mes dossiers/Atelier", "/R2-D2"], [])
    assert card_order.read("etabli") == {"ordre": ["jellyfin", "adguard-home"], "masques": ["immich"]}
    assert card_order.read("accueil-tuiles")["ordre"] == ["/Mes dossiers/Atelier", "/R2-D2"]
    assert card_order.read("accueil-mesures") == {"ordre": [], "masques": []}  # un écran ne touche pas les autres


def test_reprise_de_l_ancien_fichier(fichiers):
    (fichiers / "ordre-etabli.json").write_text(json.dumps({"ordre": ["atelier", "jellyfin"]}))
    assert card_order.read("etabli") == {"ordre": ["atelier", "jellyfin"], "masques": []}


@pytest.mark.parametrize("ordre", [None, "jellyfin", [1], [""], ["a\nb"], ["x" * 301], ["ok"] * 201, [{"id": "x"}]])
def test_ordre_refuse(ordre):
    with pytest.raises(ValueError):
        card_order.save("etabli", ordre)
    assert card_order.read("etabli") == {"ordre": [], "masques": []}


def test_ecran_inconnu():
    with pytest.raises(ValueError):
        card_order.read("../etc")
    with pytest.raises(ValueError):
        card_order.save("systeme", [])
