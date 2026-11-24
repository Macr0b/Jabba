"""Redémarrage après une mise à jour du système : automatique (4 h) ou à la main, au choix.

Certaines mises à jour (noyau, bibliothèques de base) ne prennent effet qu'au redémarrage.
Deux façons de faire :
  - « auto » : le serveur redémarre seul à 4 h, s'il y a une mise à jour en attente ;
  - « manuel » : Jabba prévient, et on redémarre quand on est devant la machine
    (conseillé si elle a déjà bloqué au démarrage).
Le choix est rangé dans /etc/jabba/redemarrage. Le minuteur de 4 h ne redémarre que
si ce fichier dit « auto » (ExecCondition) : changer d'avis ne demande que ce fichier.

Sommaire :
  interpret     : le mode en vigueur. Sans choix enregistré (installation d'avant 0.13), le minuteur décide
  apply_command : la commande root qui enregistre le choix et remet le minuteur à jour (host/scripts/restart_policy.sh)
  message       : texte de la notification quand une mise à jour attend un redémarrage
  current_mode  : {"mode": "auto"|"manuel", "choisi": bool} : redémarrage seul à 4 h, ou à la main
"""

import os

import config
from host import commands

CHOICE_FILE = "/etc/jabba/redemarrage"
MODES = ("auto", "manuel")
UNIT = "jabba-redemarrage-auto"

SERVICE = """[Unit]
Description=Jabba : redémarrage après une mise à jour qui l'exige (si choisi)
ConditionPathExists=/run/reboot-required

[Service]
Type=oneshot
ExecCondition=/bin/sh -c 'grep -qx auto /etc/jabba/redemarrage'
ExecStart=/usr/local/sbin/redemarrer
"""

TIMER = """[Unit]
Description=Jabba : redémarrage de 4 h si une mise à jour l'exige (si choisi)

[Timer]
OnCalendar=*-*-* 04:00
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
"""

RESTART_SCRIPT = """#!/bin/sh
logger -t redemarrer "Redémarrage du serveur"
exec systemctl reboot
"""


def interpret(content, timer_enabled):
    """Le mode en vigueur. Sans choix enregistré (installation d'avant 0.13), le minuteur décide."""
    mode = (content or "").strip()
    if mode in MODES:
        return {"mode": mode, "choisi": True}
    return {"mode": "auto" if timer_enabled else "manuel", "choisi": False}


def apply_command(mode):
    """La commande root qui enregistre le choix et remet le minuteur à jour (il ne redémarre que si « auto »).
    Remplace aussi un ancien service sans condition (installations d'avant 0.13)."""
    if mode not in MODES:
        raise ValueError("mode inconnu")
    from host.action_common import script

    return ["sh", "-c", script("restart_policy"), "regle", mode, CHOICE_FILE, UNIT, SERVICE, TIMER, RESTART_SCRIPT]


def message(auto):
    """Texte de la notification quand une mise à jour attend un redémarrage."""
    if auto:
        return "Une mise à jour du système le demande. Il se fera seul à 4 h."
    return "Une mise à jour du système le demande. Redémarre le serveur quand tu es devant (Système)."


# ---------------------------------------------------------------- le mode en vigueur
DEMO_FILE = os.path.join(os.environ.get("JABBA_DATA", "/data"), "redemarrage-demo.txt")


def current_mode():
    """{"mode": "auto"|"manuel", "choisi": bool} : redémarrage seul à 4 h, ou à la main."""
    if config.DEMO:
        try:
            return interpret(open(DEMO_FILE).read(), True)
        except OSError:
            return interpret("", True)
    code, content, _ = commands.run_action("lire_regle_redemarrage")
    active = commands.run_action("minuteur_redemarrage")[0] == 0
    return interpret(content if code == 0 else "", active)
