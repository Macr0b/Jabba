#!/bin/sh
# Changement d'avis sur le déménagement (services/server_move.py) : les outils arrêtés repartent, et
# la copie est effacée (le nouveau serveur ne doit pas reprendre une copie dépassée). $1 le disque ;
# il est rouvert en écriture le temps de l'effacer.
copy="$1/.demenagement-jabba"
if [ -f "$copy/outils-arretes" ]; then
  while read -r stack; do
    case "$stack" in
      /opt/stacks/*) docker compose --project-directory "$stack" start >/dev/null 2>&1 ;;
    esac
  done < "$copy/outils-arretes"
fi
reopened=0
case ",$(findmnt -no OPTIONS "$1")," in
  *,ro,*) mount -o remount,rw "$1" && reopened=1 ;;
esac
rm -rf "$copy" "$copy.tmp"
[ "$reopened" = 1 ] && mount -o remount,ro "$1"
true
