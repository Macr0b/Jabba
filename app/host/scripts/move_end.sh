#!/bin/sh
# Fin du déménagement (services/server_move.py) : 30 secondes pour que l'appli réponde, puis les
# adresses des outils et le serveur quittent Tailscale (le nouveau serveur reprend leurs noms), et
# le serveur s'éteint.
sleep 30
for container in $(docker ps --format '{{.Names}}' | grep '^adresse-'); do
  docker exec "$container" tailscale logout
done
tailscale logout
systemctl poweroff
