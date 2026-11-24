#!/bin/sh
# Redémarrage du serveur : par « redemarrer » s'il est là (il note le redémarrage au journal).
[ -x /usr/local/sbin/redemarrer ] && exec /usr/local/sbin/redemarrer
exec systemctl reboot
