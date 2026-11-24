#!/bin/sh
# Lecture seule ou écriture pour un disque déclaré : $1 son dossier, $2 « ro » ou « rw », $3 les
# droits donnés au montage pour exFAT, FAT et NTFS (qui n'ont pas de droits par fichier). Seules les
# options de sa ligne de /etc/fstab changent ; il est remonté aussitôt.
F=/etc/fstab
set -e
T=$(mktemp)
if ! P="$1" M="$2" R="$3" awk '!/^[[:space:]]*#/ && NF>=4 && $2==ENVIRON["P"] {
  shared = ($3 == "exfat" || $3 == "vfat" || $3 == "ntfs3")
  n = split($4, o, ","); s = ENVIRON["M"]
  for (i = 1; i <= n; i++) {
    if (o[i] == "ro" || o[i] == "rw") continue
    if (shared && o[i] ~ /^(uid|gid|umask|fmask|dmask)=/) continue
    s = s "," o[i]
  }
  if (shared) s = s "," ENVIRON["R"]
  $4 = s; f = 1 }
  {print} END {exit !f}' "$F" > "$T"; then
  rm -f "$T"; echo "Ce disque n'est pas déclaré sur le serveur." >&2; exit 4
fi
cp "$F" "$F.jabba-bak"
cat "$T" > "$F"
rm -f "$T"
systemctl daemon-reload
umount "$1" 2>/dev/null || true
U=$(systemd-escape --path --suffix=automount "$1")
systemctl restart "$U" || true
ls "$1" >/dev/null 2>&1 || true
