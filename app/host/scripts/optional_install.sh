#!/bin/sh
# Installe un outil en option (host/catalog.py). $0 son dossier, $1 son compose.yaml, $2 son .env
# (modèle), $3 sa préparation, $4 son port https sur Tailscale, $5 son port sur le serveur ; le mot de
# passe de l'admin arrive sur l'entrée standard. Un .env déjà là est gardé (avec ses secrets et ses
# comptes : réinstaller un outil retrouve ses données), et le script le dit (« REGLAGES_GARDES ») :
# le mot de passe tiré pour cette fois ne sert alors pas. Les marques @…@ du modèle sont remplacées
# par awk, à partir de variables d'environnement (jamais collées dans le texte du script).
set -e
umask 077
mkdir -p "$0"
cd "$0"
printf "%s" "$1" > compose.yaml
pass=$(cat)
if [ -n "$2" ] && [ -f .env ]; then
  echo "REGLAGES_GARDES"  # réinstallation : comptes et secrets de la fois d'avant
fi
if [ -n "$2" ] && [ ! -f .env ]; then
  secret=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
  secret2=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
  name=$(tailscale status --json --peers=false 2>/dev/null | sed -n 's/^ *"DNSName": "\(.*\)\.",*$/\1/p' | head -1)
  lan=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
  tsip=$(tailscale ip -4 2>/dev/null | head -1)
  user=$(getent passwd 1000 | cut -d: -f1)
  gid=$(getent passwd 1000 | cut -d: -f4)
  render=$(getent group render | cut -d: -f3)
  printf "%s\n" "$2" | S="$secret" S2="$secret2" P="$pass" T="$name" L="${lan:-127.0.0.1}" I="${tsip:-127.0.0.2}" \
    U="${user:-root}" G="${gid:-1000}" R="${render:-0}" H="$(hostname)" N="${name#*.}" awk 'BEGIN {
      marks[1] = "@SECRET_TIRE_AU_SORT@"; values[1] = ENVIRON["S"]
      marks[2] = "@MOT_DE_PASSE_ADMIN@"; values[2] = ENVIRON["P"]
      marks[3] = "@ADRESSE_TAILSCALE@"; values[3] = ENVIRON["T"]
      marks[4] = "@IP_LOCALE@"; values[4] = ENVIRON["L"]
      marks[5] = "@IP_TAILSCALE@"; values[5] = ENVIRON["I"]
      marks[6] = "@AUTRE_SECRET@"; values[6] = ENVIRON["S2"]
      marks[7] = "@COMPTE_SERVEUR@"; values[7] = ENVIRON["U"]
      marks[8] = "@UID_SERVEUR@"; values[8] = "1000"
      marks[9] = "@GID_SERVEUR@"; values[9] = ENVIRON["G"]
      marks[10] = "@GID_RENDER@"; values[10] = ENVIRON["R"]
      marks[11] = "@NOM_SERVEUR@"; values[11] = ENVIRON["H"]
      marks[12] = "@SUFFIXE_TAILSCALE@"; values[12] = ENVIRON["N"] }
    { for (n = 1; n <= 12; n++) {
        done = ""; rest = $0
        while ((i = index(rest, marks[n])) > 0) {
          done = done substr(rest, 1, i - 1) values[n]; rest = substr(rest, i + length(marks[n]))
        }
        $0 = done rest
      }
      print }' > .env
fi
[ -z "$3" ] || sh -c "$3"
docker compose up -d
[ -z "$4" ] || tailscale serve --bg --https="$4" "http://127.0.0.1:$5" || echo "Adresse https : active HTTPS Certificates dans la console Tailscale." >&2
