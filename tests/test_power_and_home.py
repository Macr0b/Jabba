"""Alimentation (secteur, batterie, consommation) et données de l'accueil (dossiers souvent ouverts)."""

import pytest

from services import home_data, power


@pytest.fixture
def usage(tmp_path, monkeypatch):
    monkeypatch.setattr(power, "CONSUMPTION_FILE", str(tmp_path / "consommation.json"))


def lines(*items_):
    return "\n".join("|".join(e) for e in items_)


@pytest.mark.parametrize(
    "output, expected",
    [
        (lines(("Mains", "1", "", "", "", "", "")), (True, None, None, None)),
        (
            lines(("Mains", "0", "", "", "", "", ""), ("Battery", "", "87", "Discharging", "9500000", "", "")),
            (False, 87, "Discharging", "batterie"),
        ),
        (lines(("Battery", "", "100", "Full", "", "", "")), (True, 100, "Full", "batterie")),  # secteur déduit
        (lines(("Battery", "", "40", "Discharging", "", "", "")), (False, 40, "Discharging", "batterie")),
        ("", (None, None, None, None)),  # pas de batterie, rien lu (mini-PC sans onduleur)
        (lines(("Mains", "x", "", "", "", "", ""), ("Battery", "", "?", "", "", "", "")), (None, None, None, None)),
        # Onduleur (NUT) : il passe avant la batterie d'un portable branché dessus.
        (lines(("UPS", "OL CHRG", "100", "", "", "")), (True, 100, "OL CHRG", "onduleur")),
        (
            lines(("Mains", "0", "", "", "", "", ""), ("UPS", "OB DISCHRG", "62.0", "", "", "")),
            (False, 62, "OB DISCHRG", "onduleur"),
        ),
        (lines(("UPS", "OB LB", "", "", "", "")), (False, None, "OB LB", "onduleur")),  # charge non donnée
        # Pilote pas encore prêt (état inconnu) : on lit la batterie du portable comme avant.
        (
            lines(("UPS", "WAIT", "", "", "", ""), ("Battery", "", "80", "Full", "", "", "")),
            (True, 80, "Full", "batterie"),
        ),
    ],
)
def test_mains_and_battery(usage, monkeypatch, output, expected):
    monkeypatch.setattr(power.commands, "run_action", lambda nom: (0, output, ""))
    assert power.read_power() == expected


def test_consumption_measured_on_battery_only(usage, monkeypatch):
    output = lines(("Battery", "", "80", "Discharging", "", "800000", "12000000"))  # 0,8 A × 12 V = 9,6 W
    monkeypatch.setattr(power.commands, "run_action", lambda nom: (0, output, ""))
    power.read_power()
    assert power.read_consumption()["latest"]["w"] == 9.6
    power.measure_consumption(14.0)
    measure = power.read_consumption()
    assert measure["latest"]["w"] == 14.0 and measure["idle"]["w"] == 9.6  # le repos garde la plus basse
    output = lines(("Battery", "", "80", "Charging", "20000000", "", ""))  # sur secteur : pas mesurable
    monkeypatch.setattr(power.commands, "run_action", lambda nom: (0, output, ""))
    power.read_power()
    assert power.read_consumption()["latest"]["w"] == 14.0


def test_consumption_measured_by_the_ups_even_on_mains(usage, monkeypatch):
    output = lines(("UPS", "OL", "100", "11", "", ""))
    monkeypatch.setattr(power.commands, "run_action", lambda nom: (0, output, ""))
    power.read_power()
    assert power.read_consumption()["latest"]["w"] == 11.0
    output = lines(("UPS", "OL", "100", "", "3", "360"))  # pas de puissance donnée : 3 % de 360 W
    monkeypatch.setattr(power.commands, "run_action", lambda nom: (0, output, ""))
    power.read_power()
    assert power.read_consumption()["latest"]["w"] == 10.8


@pytest.mark.parametrize(
    "reading, switch_off",
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
def test_when_to_shut_down(reading, switch_off):
    assert power.low_battery(*reading, threshold=15) is switch_off


def test_home_suggestions(tmp_path, monkeypatch):
    monkeypatch.setattr(home_data, "HOME_FILE", str(tmp_path / "accueil.json"))
    assert home_data.read() == {"favorites": [], "visits": {}, "ignored": []}
    for _ in range(60):
        home_data.record_visit("/Photos")
    assert len(home_data.read()["visits"]["/Photos"]) == 50  # au plus 50 ouvertures gardées
    donnees = home_data.read()
    donnees["visits"]["/Vieux"] = [1]  # ouvert il y a très longtemps : oublié à la prochaine visite
    home_data.write(donnees)
    home_data.record_visit("/Photos")
    assert "/Vieux" not in home_data.read()["visits"]


@pytest.fixture
def watcher(monkeypatch):
    import watchers

    sent, actions_run = [], []
    monkeypatch.setattr(
        watchers.notifications, "send", lambda type_, title, texte, *a, **k: sent.append((title, texte))
    )
    monkeypatch.setattr(watchers.commands, "run_action", lambda nom, **k: actions_run.append(nom) or (0, "", ""))
    monkeypatch.setattr(watchers.measurements, "server_name", lambda: "serveur-test")
    monkeypatch.setitem(power.POWER_STATE, "mains", True)
    monkeypatch.setitem(power.POWER_STATE, "shutdown_planned", False)
    return watchers, sent, actions_run


def test_outage_on_ups_then_shutdown_through_nut(watcher, monkeypatch):
    watchers, sent, started_ones = watcher
    readings = iter(
        [(False, 80, "OB", "onduleur"), (False, 70, "OB LB", "onduleur"), (True, 70, "OL CHRG", "onduleur")]
    )
    monkeypatch.setattr(watchers.power, "read_power", lambda: next(readings))
    watchers.watch_power()
    assert sent[-1][0] == "Coupure de courant" and "sur l'onduleur (80 %)" in sent[-1][1]
    watchers.watch_power()
    assert sent[-1][0].startswith("Batterie faible") and "se rallumera tout seul" in sent[-1][1]
    assert started_ones == ["arret_onduleur"] and power.POWER_STATE["source"] == "onduleur"
    watchers.watch_power()  # le courant revient avant la fin de la minute : arrêt annulé
    assert started_ones == ["arret_onduleur", "annuler_arret_batterie"] and sent[-1][0] == "Courant revenu"


def test_laptop_still_shuts_down_as_before(watcher, monkeypatch):
    watchers, sent, started_ones = watcher
    monkeypatch.setattr(watchers.power, "read_power", lambda: (False, 10, "Discharging", "batterie"))
    watchers.watch_power()
    assert started_ones == ["arret_batterie"] and "Rallume-le avec son bouton" in sent[-1][1]


def test_ups_read_by_the_server(tmp_path):
    """Le script de l'action « alimentation », avec un faux upsc : une ligne UPS, rien si NUT ne répond pas."""
    import subprocess

    from host import actions

    commande, _ = actions.build("alimentation", {})
    fake = tmp_path / "bin"
    fake.mkdir()
    values_ = {"ups.status": "OB DISCHRG", "battery.charge": "54", "ups.load": "4", "ups.realpower.nominal": "360"}
    (fake / "upsc").write_text(
        '#!/bin/sh\ncase "$2" in\n'
        + "".join(f'  {key}) echo "{val}" ;;\n' for key, val in values_.items())
        + "  *) exit 1 ;;\nesac\n"
    )
    (fake / "upsc").chmod(0o755)
    environment = {"PATH": f"{fake}:/usr/bin:/bin"}
    output = subprocess.run(commande, capture_output=True, text=True, env=environment).stdout
    assert "UPS|OB DISCHRG|54||4|360" in output.splitlines()
    (fake / "upsc").write_text("#!/bin/sh\necho 'Error: Data stale' >&2\nexit 1\n")
    output = subprocess.run(commande, capture_output=True, text=True, env=environment).stdout
    assert "UPS|" not in output


def test_ups_shutdown_can_be_cancelled():
    from host import actions

    commande, _ = actions.build("arret_onduleur", {})
    assert commande[-3:] == ["upsmon", "-c", "fsd"] and "--unit=jabba-arret" in commande
    assert actions.build("annuler_arret_batterie", {})[0] == ["systemctl", "stop", "jabba-arret.timer"]


def test_watch_schedule():
    import watchers

    everything = watchers.steps_for(0, 0)  # premier tour : tout, sauf la purge (pas encore de jour écoulé)
    assert watchers.disk_browser.purge_old_trash not in everything
    assert len(everything) == sum(len(steps) for steps in watchers.SCHEDULE.values())
    assert watchers.steps_for(1, 0) == [watchers.auto_disks.scan_disks]  # 15 s plus tard : les disques seuls
    assert watchers.watch_power in watchers.steps_for(4, 0) and watchers.dns_update.watch not in watchers.steps_for(
        4, 0
    )
