"""Redémarrage après une mise à jour du système : automatique (4 h) ou à la main, au choix.

Certaines mises à jour (noyau, bibliothèques de base) ne prennent effet qu'au redémarrage.
Deux façons de faire :
  - « auto » : le serveur redémarre seul à 4 h, s'il y a une mise à jour en attente ;
  - « manuel » : Jabba prévient, et on redémarre quand on est devant la machine
    (conseillé si elle a déjà bloqué au démarrage).
Le choix est rangé dans /etc/jabba/redemarrage. Le minuteur de 4 h ne redémarre que
si ce fichier dit « auto » (ExecCondition) : changer d'avis ne demande que ce fichier.
"""
FICHIER = "/etc/jabba/redemarrage"
MODES = ("auto", "manuel")
UNITE = "jabba-redemarrage-auto"

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

REDEMARRER = """#!/bin/sh
logger -t redemarrer "Redémarrage du serveur"
exec systemctl reboot
"""


def lire(contenu, minuteur_actif):
    """Le mode en vigueur. Sans choix enregistré (installation d'avant 0.13), le minuteur décide."""
    m = (contenu or "").strip()
    if m in MODES:
        return {"mode": m, "choisi": True}
    return {"mode": "auto" if minuteur_actif else "manuel", "choisi": False}


def script(mode):
    """Script root : enregistre le choix et remet le minuteur à jour (il ne redémarre que si « auto »).
    Remplace aussi un ancien service sans condition (installations d'avant 0.13)."""
    if mode not in MODES:
        raise ValueError("mode inconnu")
    return f"""set -e
mkdir -p /etc/jabba
echo {mode} > {FICHIER}
if [ ! -x /usr/local/sbin/redemarrer ]; then
cat > /usr/local/sbin/redemarrer <<'FIN'
{REDEMARRER}FIN
chmod 755 /usr/local/sbin/redemarrer
fi
cat > /etc/systemd/system/{UNITE}.service <<'FIN'
{SERVICE}FIN
cat > /etc/systemd/system/{UNITE}.timer <<'FIN'
{TIMER}FIN
systemctl daemon-reload
systemctl enable --now {UNITE}.timer
"""


def message(auto):
    """Texte de la notification quand une mise à jour attend un redémarrage."""
    if auto:
        return "Une mise à jour du système le demande. Il se fera seul à 4 h."
    return "Une mise à jour du système le demande. Redémarre le serveur quand tu es devant (Système)."


# ---- ajouté au découpage de main.py
import os
import config
from host import commands


# ---------------------------------------------------------------- redémarrage après une mise à jour
REDEM_DEMO = os.path.join(os.environ.get("JABBA_DATA", "/data"), "redemarrage-demo.txt")


def mode_redemarrage():
    """{"mode": "auto"|"manuel", "choisi": bool} : redémarrage seul à 4 h, ou à la main."""
    if config.DEMO:
        try:
            return lire(open(REDEM_DEMO).read(), True)
        except OSError:
            return lire("", True)
    code, contenu, _ = commands.hote(["cat", FICHIER])
    actif = commands.hote(["systemctl", "is-enabled", "--quiet", f"{UNITE}.timer"])[0] == 0
    return lire(contenu if code == 0 else "", actif)
