"""Mesures du serveur : processeur, mémoire, température, espace disque, durée de marche.

Sommaire :
  cpu_percent  : la charge du processeur, en %
  memory       : la mémoire utilisée et totale
  temperature  : la température du processeur, en °C (ou None)
  disk_space   : l'espace total et utilisé de chacun de ces dossiers
  uptime       : depuis combien de temps le serveur tourne
  server_name  : le nom du serveur
"""

import os
import time

import config
from host import actions, commands


# ---------------------------------------------------------------- mesures
def cpu_percent():
    def lire():
        with open("/proc/stat") as file:
            values = [int(fields) for fields in file.readline().split()[1:]]
        return sum(values), values[3] + values[4]

    total_before, idle_before = lire()
    time.sleep(0.3)
    total_after, idle_after = lire()
    return round(100 * (1 - (idle_after - idle_before) / max(1, total_after - total_before)))


def memory():
    info = {}
    with open("/proc/meminfo") as file:
        for line in file:
            key, value = line.split(":")
            info[key] = int(value.split()[0]) * 1024
    return {"total": info["MemTotal"], "used": info["MemTotal"] - info["MemAvailable"]}


def temperature():
    best = None
    base = "/sys/class/thermal"
    try:
        for zone in os.listdir(base):
            if not zone.startswith("thermal_zone"):
                continue
            try:
                media_type = open(f"{base}/{zone}/type").read().strip()
                degrees = int(open(f"{base}/{zone}/temp").read()) / 1000
            except OSError:
                continue
            if media_type == "x86_pkg_temp":
                return round(degrees)
            if best is None or degrees > best:
                best = degrees
    except OSError:
        pass
    return round(best) if best is not None else None


def disk_space(paths):
    try:
        code, output, _ = commands.run_action("espace_disques", paths=paths)
    except actions.ActionError:
        return {}
    result = {}
    for line in output.splitlines()[1:]:
        shares = line.split()
        if len(shares) == 3:
            result[shares[0]] = {"total": int(shares[1]), "used": int(shares[2])}
    return result


def uptime():
    with open("/proc/uptime") as file:
        return int(float(file.read().split()[0]))


def server_name():
    return (
        "Serveur-démo"
        if config.DEMO
        else (commands.run_action("nom_du_serveur")[1].strip().capitalize() or "Le serveur")
    )
