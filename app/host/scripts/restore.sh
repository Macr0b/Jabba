#!/bin/sh
# Restauration restic (services/backups.py) : $0 le dossier Restaurations, puis les arguments de
# restic. Les fichiers revenus sont donnés au compte du serveur.
restic "$@" && chown -R 1000:1000 "$0"
