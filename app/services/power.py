"""Alimentation du serveur : secteur ou batterie, et consommation électrique.

Deux sources de batterie : celle d'un portable, ou un onduleur branché en USB (lu par NUT, installé
par le script d'installation). L'onduleur passe en premier : il protège aussi la box.

Sommaire :
  read_power : secteur ou non, pourcentage, état, et la source ("onduleur", "batterie" ou None)
  low_battery : vrai quand il faut éteindre (sous le seuil, ou l'onduleur annonce sa batterie faible)
  measure_consumption, read_consumption : consommation mesurée (watts)
  interne : _ups (la ligne de l'onduleur)
"""

import json
import os
import time

from host import commands

CONSUMPTION_FILE = os.path.join(os.environ.get("JABBA_DATA", "/data"), "consommation.json")


# ---------------------------------------------------------------- surveillance (tourne en fond)
POWER_STATE = {"secteur": None, "batterie": None, "etat": None, "source": None, "arret_prevu": False}


def _ups(status, charge, real_power, load, nominal):
    """La ligne de l'onduleur : (secteur, pourcentage, état), ou None si elle est illisible."""
    flags = status.split()
    if "OL" not in flags and "OB" not in flags:
        return None
    battery = round(float(charge)) if charge.strip().replace(".", "", 1).isdigit() else None
    # L'onduleur mesure ce que tire le serveur (et la box), sur secteur comme sur batterie.
    try:
        watts = float(real_power) if real_power.strip() else float(load) * float(nominal) / 100
    except ValueError:
        watts = None
    if watts and 0.5 < watts < 200:
        measure_consumption(round(watts, 1))
    return "OL" in flags, battery, status.strip()


def read_power():
    _, output, _ = commands.run_action("alimentation")
    on_mains, battery, state = None, None, None
    for line in output.splitlines():
        if line.startswith("UPS|"):
            ups = _ups(*(line.split("|") + [""] * 6)[1:6])
            if ups:
                return (*ups, "onduleur")
    for line in output.splitlines():
        fields, online, capacity, status, power, intensite, voltage = (line.split("|") + [""] * 7)[:7]
        if fields == "Mains" and online.strip().isdigit():
            on_mains = (on_mains or False) or online.strip() == "1"
        elif fields == "Battery" and capacity.strip().isdigit():
            battery, state = int(capacity), status.strip()
            # Consommation : mesurable seulement sur batterie (sur secteur, la batterie ne voit pas ce que tire le PC).
            try:
                watts = int(power) / 1e6 if power.strip() else int(intensite) * int(voltage) / 1e12
            except ValueError:
                watts = None
            if state == "Discharging" and watts and 0.5 < watts < 200:
                measure_consumption(round(watts, 1))
    if on_mains is None and state:
        on_mains = state != "Discharging"
    return on_mains, battery, state, ("batterie" if battery is not None else None)


def low_battery(on_mains, battery, state, source, threshold):
    """Vrai quand il faut éteindre : sur batterie et sous le seuil, ou l'onduleur annonce « LB »."""
    if on_mains is not False:
        return False
    if source == "onduleur" and "LB" in (state or "").split():
        return True
    return battery is not None and battery <= threshold


def measure_consumption(watts):
    """Garde la dernière consommation mesurée (et la plus basse, celle du repos), dans donnees/."""
    try:
        with open(CONSUMPTION_FILE) as file:
            data = json.load(file)
    except (OSError, ValueError):
        data = {}
    data["derniere"] = {"w": watts, "t": int(time.time())}
    if not data.get("repos") or watts < data["repos"]["w"]:
        data["repos"] = {"w": watts, "t": int(time.time())}
    with open(CONSUMPTION_FILE, "w") as file:
        json.dump(data, file)


def read_consumption():
    try:
        with open(CONSUMPTION_FILE) as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}
