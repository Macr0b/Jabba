#!/bin/sh
# L'alimentation du serveur (services/power.py) : une ligne par source de courant du noyau
# (secteur, batterie du portable), puis l'onduleur s'il y en a un (NUT) :
#   type|branché|charge|état|puissance|intensité|tension
#   UPS|état|charge|puissance|charge en %|puissance nominale
for supply in /sys/class/power_supply/*; do
  [ -e "$supply/type" ] || continue
  echo "$(cat "$supply/type")|$(cat "$supply/online" 2>/dev/null)|$(cat "$supply/capacity" 2>/dev/null)|$(cat "$supply/status" 2>/dev/null)|$(cat "$supply/power_now" 2>/dev/null)|$(cat "$supply/current_now" 2>/dev/null)|$(cat "$supply/voltage_now" 2>/dev/null)"
done
if command -v upsc >/dev/null 2>&1 && ups_status=$(upsc onduleur@localhost ups.status 2>/dev/null); then
  ups() { upsc onduleur@localhost "$1" 2>/dev/null; }
  echo "UPS|$ups_status|$(ups battery.charge)|$(ups ups.realpower)|$(ups ups.load)|$(ups ups.realpower.nominal)"
fi
