#!/bin/sh
# Pose un compose.yaml et lance la pile (antivirus) : $0 le dossier, $1 le contenu du compose.yaml.
set -e
mkdir -p "$0"
cd "$0"
printf "%s" "$1" > compose.yaml
docker compose up -d
