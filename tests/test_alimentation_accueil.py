"""Alimentation (secteur, batterie, consommation) et données de l'accueil (dossiers souvent ouverts)."""

import pytest

from services import home_data, power


@pytest.fixture
def conso(tmp_path, monkeypatch):
    monkeypatch.setattr(power, "CONSUMPTION_FILE", str(tmp_path / "consommation.json"))


def lignes(*elements):
    return "\n".join("|".join(e) for e in elements)


@pytest.mark.parametrize(
    "sortie, attendu",
    [
        (lignes(("Mains", "1", "", "", "", "", "")), (True, None, None)),
        (
            lignes(("Mains", "0", "", "", "", "", ""), ("Battery", "", "87", "Discharging", "9500000", "", "")),
            (False, 87, "Discharging"),
        ),
        (lignes(("Battery", "", "100", "Full", "", "", "")), (True, 100, "Full")),  # pas de secteur lu : on déduit
        (lignes(("Battery", "", "40", "Discharging", "", "", "")), (False, 40, "Discharging")),
        ("", (None, None, None)),  # pas de batterie, rien lu (mini-PC)
        (lignes(("Mains", "x", "", "", "", "", ""), ("Battery", "", "?", "", "", "", "")), (None, None, None)),
    ],
)
def test_secteur_et_batterie(conso, monkeypatch, sortie, attendu):
    monkeypatch.setattr(power.commands, "run_action", lambda nom: (0, sortie, ""))
    assert power.read_power() == attendu


def test_consommation_mesuree_sur_batterie_seulement(conso, monkeypatch):
    sortie = lignes(("Battery", "", "80", "Discharging", "", "800000", "12000000"))  # 0,8 A × 12 V = 9,6 W
    monkeypatch.setattr(power.commands, "run_action", lambda nom: (0, sortie, ""))
    power.read_power()
    assert power.read_consumption()["derniere"]["w"] == 9.6
    power.measure_consumption(14.0)
    mesure = power.read_consumption()
    assert mesure["derniere"]["w"] == 14.0 and mesure["repos"]["w"] == 9.6  # le repos garde la plus basse
    sortie = lignes(("Battery", "", "80", "Charging", "20000000", "", ""))  # sur secteur : pas mesurable
    monkeypatch.setattr(power.commands, "run_action", lambda nom: (0, sortie, ""))
    power.read_power()
    assert power.read_consumption()["derniere"]["w"] == 14.0


def test_suggestions_d_accueil(tmp_path, monkeypatch):
    monkeypatch.setattr(home_data, "HOME_FILE", str(tmp_path / "accueil.json"))
    assert home_data.read() == {"favoris": [], "visites": {}, "ignores": []}
    for _ in range(60):
        home_data.record_visit("/Photos")
    assert len(home_data.read()["visites"]["/Photos"]) == 50  # au plus 50 ouvertures gardées
    donnees = home_data.read()
    donnees["visites"]["/Vieux"] = [1]  # ouvert il y a très longtemps : oublié à la prochaine visite
    home_data.write(donnees)
    home_data.record_visit("/Photos")
    assert "/Vieux" not in home_data.read()["visites"]
