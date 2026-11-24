#!/bin/sh
# Oublie un disque ajouté par l'appli : $1 son dossier, $2 le commentaire posé au-dessus de sa ligne.
# Sa ligne de /etc/fstab et ce commentaire sont retirés (copie de l'ancien fichier : fstab.jabba-bak).
F=/etc/fstab
U=$(systemd-escape --path --suffix=automount "$1")
systemctl stop "$U" 2>/dev/null
umount "$1" 2>/dev/null
set -e
T=$(mktemp)
P="$1" C="$2" awk '!/^[[:space:]]*#/ && NF>=2 && $2==ENVIRON["P"] {next}
  {l = $0; gsub(/^[ \t]+|[ \t]+$/, "", l); if (l == ENVIRON["C"]) next}
  {print}' "$F" > "$T"
cp "$F" "$F.jabba-bak"
cat "$T" > "$F"
rm -f "$T"
systemctl daemon-reload
rmdir "$1" 2>/dev/null || true
