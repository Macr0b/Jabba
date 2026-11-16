"""services.measurements"""
import os
import time
import config
from host import commands


# ---------------------------------------------------------------- mesures
def cpu_percent():
    def lire():
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:]]
        return sum(v), v[3] + v[4]

    t1, i1 = lire()
    time.sleep(0.3)
    t2, i2 = lire()
    return round(100 * (1 - (i2 - i1) / max(1, t2 - t1)))


def memory():
    info = {}
    with open("/proc/meminfo") as f:
        for ligne in f:
            cle, val = ligne.split(":")
            info[cle] = int(val.split()[0]) * 1024
    return {"total": info["MemTotal"], "utilise": info["MemTotal"] - info["MemAvailable"]}


def temperature():
    meilleure = None
    base = "/sys/class/thermal"
    try:
        for zone in os.listdir(base):
            if not zone.startswith("thermal_zone"):
                continue
            try:
                type_ = open(f"{base}/{zone}/type").read().strip()
                t = int(open(f"{base}/{zone}/temp").read()) / 1000
            except OSError:
                continue
            if type_ == "x86_pkg_temp":
                return round(t)
            if meilleure is None or t > meilleure:
                meilleure = t
    except OSError:
        pass
    return round(meilleure) if meilleure is not None else None


def disk_space(chemins):
    code, sortie, _ = commands.run_on_host(["df", "-B1", "--output=target,size,used"] + chemins)
    res = {}
    for ligne in sortie.splitlines()[1:]:
        p = ligne.split()
        if len(p) == 3:
            res[p[0]] = {"total": int(p[1]), "utilise": int(p[2])}
    return res


def uptime():
    with open("/proc/uptime") as f:
        return int(float(f.read().split()[0]))


def server_name():
    return "Serveur-démo" if config.DEMO else (commands.run_on_host(["hostname"])[1].strip().capitalize() or "Le serveur")
