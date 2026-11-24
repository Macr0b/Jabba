#!/bin/sh
# Accès par le domaine (host/actions_tools.py) : $0 le dossier de Caddy, $1 son compose.yaml, $2 son
# Caddyfile (leur contenu en arguments, jamais collé dans le texte du script).
set -e
mkdir -p "$0"
cd "$0"
printf "%s" "$1" > compose.yaml
printf "%s" "$2" > Caddyfile
docker compose up -d
docker compose exec -T acces-domaine caddy reload --config /etc/caddy/Caddyfile || true
