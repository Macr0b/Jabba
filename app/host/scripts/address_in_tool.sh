#!/bin/sh
# L'outil apprend sa nouvelle adresse (liens, protection des formulaires) : une ligne de son .env,
# puis relance. $0 l'outil, $1 le nom de la variable (fixé dans host/catalog.py), $2 l'adresse.
cd "/opt/stacks/$0" || exit 1
[ -f .env ] && grep -q "^$1=" .env && sed -i "s#^$1=.*#$1=https://$2#" .env && docker compose up -d
