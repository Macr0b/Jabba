"""services.power"""
import json
import os
import time
from host import commands


FICHIER_CONSO = os.path.join(os.environ.get("JABBA_DATA", "/data"), "consommation.json")


# ---------------------------------------------------------------- surveillance (tourne en fond)
COURANT = {"secteur": None, "batterie": None, "etat": None, "arret_prevu": False}


def lire_courant():
    script = ('for f in /sys/class/power_supply/*; do [ -e "$f/type" ] || continue; '
              'echo "$(cat $f/type)|$(cat $f/online 2>/dev/null)|$(cat $f/capacity 2>/dev/null)|$(cat $f/status 2>/dev/null)'
              '|$(cat $f/power_now 2>/dev/null)|$(cat $f/current_now 2>/dev/null)|$(cat $f/voltage_now 2>/dev/null)"; done')
    _, sortie, _ = commands.hote_sh(script, timeout=10)
    secteur, batterie, etat = None, None, None
    for ligne in sortie.splitlines():
        t, en_ligne, capa, statut, puissance, intensite, tension = (ligne.split("|") + [""] * 7)[:7]
        if t == "Mains" and en_ligne.strip().isdigit():
            secteur = (secteur or False) or en_ligne.strip() == "1"
        elif t == "Battery" and capa.strip().isdigit():
            batterie, etat = int(capa), statut.strip()
            # Consommation : mesurable seulement sur batterie (sur secteur, la batterie ne voit pas ce que tire le PC).
            try:
                w = int(puissance) / 1e6 if puissance.strip() else int(intensite) * int(tension) / 1e12
            except ValueError:
                w = None
            if etat == "Discharging" and w and 0.5 < w < 200:
                mesure_conso(round(w, 1))
    if secteur is None and etat:
        secteur = etat != "Discharging"
    return secteur, batterie, etat


def mesure_conso(w):
    """Garde la dernière consommation mesurée (et la plus basse, celle du repos), dans donnees/."""
    try:
        with open(FICHIER_CONSO) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d["derniere"] = {"w": w, "t": int(time.time())}
    if not d.get("repos") or w < d["repos"]["w"]:
        d["repos"] = {"w": w, "t": int(time.time())}
    with open(FICHIER_CONSO, "w") as f:
        json.dump(d, f)


def lire_conso():
    try:
        with open(FICHIER_CONSO) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}
