#!/bin/sh
# Redémarrage après une mise à jour (services/restart_policy.py) : enregistre le choix et remet le
# minuteur de 4 h à jour (il ne redémarre que si le choix est « auto »). Remplace aussi un ancien
# service sans condition (installations d'avant 0.13).
# $1 le mode, $2 le fichier du choix, $3 le nom de l'unité, $4 le service, $5 le minuteur, $6 le
# script « redemarrer » (posé s'il manque). Les contenus arrivent en arguments, jamais dans le texte.
set -e
mkdir -p "$(dirname "$2")"
echo "$1" > "$2"
if [ ! -x /usr/local/sbin/redemarrer ]; then
  printf "%s" "$6" > /usr/local/sbin/redemarrer
  chmod 755 /usr/local/sbin/redemarrer
fi
printf "%s" "$4" > "/etc/systemd/system/$3.service"
printf "%s" "$5" > "/etc/systemd/system/$3.timer"
systemctl daemon-reload
systemctl enable --now "$3.timer"
