"""services.measurements"""

import os
import time
import config
from host import commands


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
    return {"total": info["MemTotal"], "utilise": info["MemTotal"] - info["MemAvailable"]}


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
    code, output, _ = commands.run_on_host(["df", "-B1", "--output=target,size,used"] + paths)
    result = {}
    for line in output.splitlines()[1:]:
        shares = line.split()
        if len(shares) == 3:
            result[shares[0]] = {"total": int(shares[1]), "utilise": int(shares[2])}
    return result


def uptime():
    with open("/proc/uptime") as file:
        return int(float(file.read().split()[0]))


def server_name():
    return (
        "Serveur-démo" if config.DEMO else (commands.run_on_host(["hostname"])[1].strip().capitalize() or "Le serveur")
    )
