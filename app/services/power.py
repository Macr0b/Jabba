"""services.power"""
import json
import os
import time
from host import commands


CONSUMPTION_FILE = os.path.join(os.environ.get("JABBA_DATA", "/data"), "consommation.json")


# ---------------------------------------------------------------- surveillance (tourne en fond)
POWER_STATE = {"secteur": None, "batterie": None, "etat": None, "arret_prevu": False}


def read_power():
    script = ('for f in /sys/class/power_supply/*; do [ -e "$f/type" ] || continue; '
              'echo "$(cat $f/type)|$(cat $f/online 2>/dev/null)|$(cat $f/capacity 2>/dev/null)|$(cat $f/status 2>/dev/null)'
              '|$(cat $f/power_now 2>/dev/null)|$(cat $f/current_now 2>/dev/null)|$(cat $f/voltage_now 2>/dev/null)"; done')
    _, output, _ = commands.run_shell_on_host(script, timeout=10)
    on_mains, battery, state = None, None, None
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
    return on_mains, battery, state


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
