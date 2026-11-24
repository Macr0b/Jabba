#!/bin/sh
# Pare-feu du réseau des invités (services/guest_network.py) : depuis l'interface $1, les ports qui
# suivent sont fermés aux outils Docker (chaîne JABBA-WIFI, accrochée à DOCKER-USER). Rien n'est
# réécrit si les règles sont déjà les bonnes. Code 1 si une règle n'a pas pu être posée.
interface="$1"
shift
status=0
for tables in iptables ip6tables; do
  "$tables" -S DOCKER-USER >/dev/null 2>&1 || continue
  wanted=$(for port in "$@"; do echo "-A JABBA-WIFI -i $interface -p tcp -m conntrack --ctorigdstport $port --ctdir ORIGINAL -j DROP"; done)
  current=$("$tables" -S JABBA-WIFI 2>/dev/null | grep '^-A ')
  if [ "$wanted" = "$current" ] && "$tables" -C DOCKER-USER -j JABBA-WIFI 2>/dev/null; then continue; fi
  "$tables" -N JABBA-WIFI 2>/dev/null
  "$tables" -F JABBA-WIFI || { status=1; continue; }
  for port in "$@"; do
    "$tables" -A JABBA-WIFI -i "$interface" -p tcp -m conntrack --ctorigdstport "$port" --ctdir ORIGINAL -j DROP || status=1
  done
  "$tables" -C DOCKER-USER -j JABBA-WIFI 2>/dev/null || "$tables" -I DOCKER-USER -j JABBA-WIFI || status=1
done
exit $status
