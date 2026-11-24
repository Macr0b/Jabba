#!/bin/sh
# Déclare un disque ajouté par l'appli (services/auto_disks.py) : $1 son dossier sous /srv/disques,
# $2 le commentaire et $3 la ligne à ajouter à /etc/fstab (écrites ligne par ligne : l'appli n'envoie
# jamais le fichier entier). Puis le disque s'ouvre tout seul à la première lecture (automount).
F=/etc/fstab
set -e
if P="$1" awk '!/^[[:space:]]*#/ && $2==ENVIRON["P"] {f=1} END {exit !f}' "$F"; then
  echo "Ce dossier est déjà déclaré." >&2; exit 3
fi
mkdir -p "$1"
cp "$F" "$F.jabba-bak"
[ -z "$(tail -c1 "$F")" ] || echo >> "$F"
printf '%s\n%s\n' "$2" "$3" >> "$F"
systemctl daemon-reload
U=$(systemd-escape --path --suffix=automount "$1")
systemctl start "$U"
ls "$1" >/dev/null 2>&1 || true
