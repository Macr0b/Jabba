#!/bin/sh
# Désinstalle un outil en option (host/catalog.py) : $0 son dossier, $1 son port https sur Tailscale
# (ou vide), $2 son ménage (ou vide : par exemple la tâche planifiée de Nextcloud). Ses données restent.
cd "$0" && docker compose down
[ -z "$1" ] || tailscale serve --https="$1" off || true
docker stop "adresse-$(basename "$0")" 2>/dev/null || true
[ -z "$2" ] || sh -c "$2"
