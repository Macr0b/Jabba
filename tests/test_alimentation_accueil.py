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
        (lignes(("Mains", "1", "", "", "", "", "")), (True, None, None, None)),
        (
            lignes(("Mains", "0", "", "", "", "", ""), ("Battery", "", "87", "Discharging", "9500000", "", "")),
            (False, 87, "Discharging", "batterie"),
        ),
        (lignes(("Battery", "", "100", "Full", "", "", "")), (True, 100, "Full", "batterie")),  # secteur déduit
        (lignes(("Battery", "", "40", "Discharging", "", "", "")), (False, 40, "Discharging", "batterie")),
        ("", (None, None, None, None)),  # pas de batterie, rien lu (mini-PC sans onduleur)
        (lignes(("Mains", "x", "", "", "", "", ""), ("Battery", "", "?", "", "", "", "")), (None, None, None, None)),
        # Onduleur (NUT) : il passe avant la batterie d'un portable branché dessus.
        (lignes(("UPS", "OL CHRG", "100", "", "", "")), (True, 100, "OL CHRG", "onduleur")),
        (
            lignes(("Mains", "0", "", "", "", "", ""), ("UPS", "OB DISCHRG", "62.0", "", "", "")),
            (False, 62, "OB DISCHRG", "onduleur"),
        ),
        (lignes(("UPS", "OB LB", "", "", "", "")), (False, None, "OB LB", "onduleur")),  # charge non donnée
        # Pilote pas encore prêt (état inconnu) : on lit la batterie du portable comme avant.
        (
            lignes(("UPS", "WAIT", "", "", "", ""), ("Battery", "", "80", "Full", "", "", "")),
            (True, 80, "Full", "batterie"),
        ),
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


def test_consommation_mesuree_par_l_onduleur_meme_sur_secteur(conso, monkeypatch):
    sortie = lignes(("UPS", "OL", "100", "11", "", ""))
    monkeypatch.setattr(power.commands, "run_action", lambda nom: (0, sortie, ""))
    power.read_power()
    assert power.read_consumption()["derniere"]["w"] == 11.0
    sortie = lignes(("UPS", "OL", "100", "", "3", "360"))  # pas de puissance donnée : 3 % de 360 W
    monkeypatch.setattr(power.commands, "run_action", lambda nom: (0, sortie, ""))
    power.read_power()
    assert power.read_consumption()["derniere"]["w"] == 10.8


@pytest.mark.parametrize(
    "lecture, eteindre",
    [
        ((True, 5, "OL", "onduleur"), False),  # sur secteur : jamais
        ((None, None, None, None), False),
        ((False, 60, "OB", "onduleur"), False),
        ((False, 60, "OB LB", "onduleur"), True),  # l'onduleur annonce sa batterie faible
        ((False, 15, "OB", "onduleur"), True),  # ou sous le seuil
        ((False, 16, "Discharging", "batterie"), False),
        ((False, 15, "Discharging", "batterie"), True),
        ((False, None, "LB", "batterie"), False),  # « LB » ne veut rien dire pour un portable
    ],
)
def test_quand_eteindre(lecture, eteindre):
    assert power.low_battery(*lecture, threshold=15) is eteindre


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


@pytest.fixture
def surveillance(monkeypatch):
    import watchers

    envois, actions_lancees = [], []
    monkeypatch.setattr(
        watchers.notifications, "send", lambda type_, titre, texte, *a, **k: envois.append((titre, texte))
    )
    monkeypatch.setattr(watchers.commands, "run_action", lambda nom, **k: actions_lancees.append(nom) or (0, "", ""))
    monkeypatch.setattr(watchers.measurements, "server_name", lambda: "serveur-test")
    monkeypatch.setitem(power.POWER_STATE, "secteur", True)
    monkeypatch.setitem(power.POWER_STATE, "arret_prevu", False)
    return watchers, envois, actions_lancees


def test_coupure_sur_onduleur_puis_arret_par_nut(surveillance, monkeypatch):
    watchers, envois, lancees = surveillance
    lectures = iter(
        [(False, 80, "OB", "onduleur"), (False, 70, "OB LB", "onduleur"), (True, 70, "OL CHRG", "onduleur")]
    )
    monkeypatch.setattr(watchers.power, "read_power", lambda: next(lectures))
    watchers.watch_power()
    assert envois[-1][0] == "Coupure de courant" and "sur l'onduleur (80 %)" in envois[-1][1]
    watchers.watch_power()
    assert envois[-1][0].startswith("Batterie faible") and "se rallumera tout seul" in envois[-1][1]
    assert lancees == ["arret_onduleur"] and power.POWER_STATE["source"] == "onduleur"
    watchers.watch_power()  # le courant revient avant la fin de la minute : arrêt annulé
    assert lancees == ["arret_onduleur", "annuler_arret_batterie"] and envois[-1][0] == "Courant revenu"


def test_portable_toujours_arrete_comme_avant(surveillance, monkeypatch):
    watchers, envois, lancees = surveillance
    monkeypatch.setattr(watchers.power, "read_power", lambda: (False, 10, "Discharging", "batterie"))
    watchers.watch_power()
    assert lancees == ["arret_batterie"] and "Rallume-le avec son bouton" in envois[-1][1]


def test_lecture_de_l_onduleur_par_le_serveur(tmp_path):
    """Le script de l'action « alimentation », avec un faux upsc : une ligne UPS, rien si NUT ne répond pas."""
    import subprocess

    from host import actions

    commande, _ = actions.build("alimentation", {})
    faux = tmp_path / "bin"
    faux.mkdir()
    valeurs = {"ups.status": "OB DISCHRG", "battery.charge": "54", "ups.load": "4", "ups.realpower.nominal": "360"}
    (faux / "upsc").write_text(
        '#!/bin/sh\ncase "$2" in\n'
        + "".join(f'  {cle}) echo "{val}" ;;\n' for cle, val in valeurs.items())
        + "  *) exit 1 ;;\nesac\n"
    )
    (faux / "upsc").chmod(0o755)
    environnement = {"PATH": f"{faux}:/usr/bin:/bin"}
    sortie = subprocess.run(commande, capture_output=True, text=True, env=environnement).stdout
    assert "UPS|OB DISCHRG|54||4|360" in sortie.splitlines()
    (faux / "upsc").write_text("#!/bin/sh\necho 'Error: Data stale' >&2\nexit 1\n")
    sortie = subprocess.run(commande, capture_output=True, text=True, env=environnement).stdout
    assert "UPS|" not in sortie


def test_arret_par_l_onduleur_annulable():
    from host import actions

    commande, _ = actions.build("arret_onduleur", {})
    assert commande[-3:] == ["upsmon", "-c", "fsd"] and "--unit=jabba-arret" in commande
    assert actions.build("annuler_arret_batterie", {})[0] == ["systemctl", "stop", "jabba-arret.timer"]


def test_calendrier_de_la_surveillance():
    import watchers

    tout = watchers.steps_for(0, 0)  # premier tour : tout, sauf la purge (pas encore de jour écoulé)
    assert watchers.disk_browser.purge_old_trash not in tout
    assert len(tout) == sum(len(etapes) for etapes in watchers.SCHEDULE.values())
    assert watchers.steps_for(1, 0) == [watchers.auto_disks.scan_disks]  # 15 s plus tard : les disques seuls
    assert watchers.watch_power in watchers.steps_for(4, 0) and watchers.dns_update.watch not in watchers.steps_for(
        4, 0
    )
