#!/bin/sh
# L'adresse à part d'un outil (services/tool_addresses.py) : un conteneur Tailscale « adresse-<outil> »
# sur le réseau du serveur, qui renvoie https://<outil>.tailxxxx.ts.net vers 127.0.0.1:<port>. Mode
# « userspace », sans toucher au Tailscale du serveur ; inscription une seule fois (TS_AUTH_ONCE),
# l'état est gardé dans ./etat. $0 l'outil, $1 son port ; la clé d'inscription (usage unique,
# 1 heure) arrive sur l'entrée standard. Un conteneur du même nom dans une autre pile est gardé
# (réponse « DEJA »).
set -e
# Déjà une adresse d'une autre installation (ancienne pile « adresses » du guide) : on la garde et on la rallume.
projet=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "adresse-$0" 2>/dev/null || true)
if [ -n "$projet" ] && [ "$projet" != "adresse-$0" ]; then
  docker start "adresse-$0" >/dev/null
  echo DEJA
  exit 0
fi
umask 077
mkdir -p "/opt/stacks/adresse-$0/etat"
cd "/opt/stacks/adresse-$0"
printf 'TS_AUTHKEY=%s\n' "$(cat)" > .env
# shellcheck disable=SC2016  # ${TS_CERT_DOMAIN} : remplacé par le conteneur Tailscale, pas par le shell
printf '{"TCP":{"443":{"HTTPS":true}},"Web":{"${TS_CERT_DOMAIN}:443":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:%s"}}}}}\n' "$1" > serve.json
cat > compose.yaml <<FIN
services:
  adresse:
    image: tailscale/tailscale:latest
    container_name: adresse-$0
    network_mode: host
    env_file: .env
    environment:
      - TS_HOSTNAME=$0
      - TS_STATE_DIR=/var/lib/tailscale
      - TS_SERVE_CONFIG=/config/serve.json
      - TS_USERSPACE=true
      - TS_AUTH_ONCE=true
      - TS_TAILSCALED_EXTRA_ARGS=--port=0
    volumes:
      - ./etat:/var/lib/tailscale
      - ./serve.json:/config/serve.json:ro
    restart: unless-stopped
FIN
docker compose up -d
