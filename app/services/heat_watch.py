"""Alerte de température : le processeur du serveur reste trop chaud.

Une alerte quand il reste au-dessus de HOT (85 °C) pendant LASTING (10 minutes) d'affilée, puis une
autre quand il est redescendu sous COOL (75 °C). Un pic de quelques minutes (une analyse de photos,
une mise à jour) ne prévient pas. Interrupteur : Système → Alertes → « Température ».

Sommaire :
  watch(now, degrees) : à appeler chaque minute ; renvoie "chaud", "revenu" ou None (rien à dire)
  HOT, COOL, LASTING, STATE
"""

import time

from services import measurements, notifications

HOT = 85
COOL = 75
LASTING = 10 * 60
STATE = {"depuis": None, "alerte": False}


def watch(now=None, degrees=None):
    now = time.time() if now is None else now
    degrees = measurements.temperature() if degrees is None else degrees
    if degrees is None:  # pas de sonde lisible (machine virtuelle…)
        return None
    if degrees >= HOT:
        if STATE["depuis"] is None:
            STATE["depuis"] = now
        if not STATE["alerte"] and now - STATE["depuis"] >= LASTING:
            STATE["alerte"] = True
            notifications.send(
                "temperature",
                f"Le serveur chauffe : {round(degrees)} °C",
                "Au-dessus de 85 °C depuis 10 minutes. Regarde quel outil travaille (Établi), vérifie que les "
                "aérations ne sont ni bouchées ni posées sur un tissu, et dépoussière-les si besoin.",
                "/#/accueil",
                family="temperature",
            )
            return "chaud"
        return None
    if degrees < COOL:
        STATE["depuis"] = None
        if STATE["alerte"]:
            STATE["alerte"] = False
            notifications.send(
                "temperature",
                f"Le serveur a refroidi : {round(degrees)} °C",
                "Sa température est revenue à la normale.",
                "/#/accueil",
                family="temperature",
            )
            return "revenu"
    return None
