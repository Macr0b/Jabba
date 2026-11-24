#!/bin/sh
# Arrête une pile, retire ses volumes puis son dossier (antivirus, accès par le domaine) : $0 le
# dossier. S'il ne s'arrête pas, le dossier reste (rien ne tourne sans son dossier).
cd "$0" && docker compose down -v && rm -rf "$0"
