#!/bin/sh
# Retire en entier un outil que Jabba ne connaît plus (ntfy…) : $0 son dossier dans /opt/stacks.
# Ses adresses Tailscale (un port https qui renvoie vers un de ses ports), ses conteneurs et volumes,
# son adresse à part, puis son dossier. Rien n'est gardé : il n'existe plus dans Jabba.
name=$(basename "$0")
cd "$0" || exit 1
ports=$(docker compose ps -q | xargs -r docker inspect --format '{{range .NetworkSettings.Ports}}{{range .}}{{.HostPort}} {{end}}{{end}}' | tr ' ' '\n' | sort -u)
for port in $ports; do
  tailscale serve --https="$port" off 2>/dev/null || true
done
docker compose down -v --remove-orphans || exit 1
docker rm -f "adresse-$name" 2>/dev/null || true
rm -rf "/opt/stacks/adresse-$name" "$0"
