#!/bin/bash
# Le déménagement du serveur (services/server_move.py) : arrête les outils, puis copie tout dans
# <disque>/.demenagement-jabba ; si ça rate, les outils repartent et la copie inachevée est effacée.
# Les valeurs arrivent en tête du script, posées par server_move.move_script() :
#   disk, copy (le dossier du déménagement), state_file, with_files (0 ou 1), version.
# shellcheck disable=SC2154  # valeurs posées en tête par server_move.move_script()
: "${disk:?}" "${copy:?}" "${state_file:?}" "${with_files:?}" "${version:?}"
set -uo pipefail
started=$(date +%s)
step=disque
reopened=0
needed=0
free=0
# shellcheck disable=SC2329  # appelée par trap
finish() {
  code=$?
  if [ "$code" != 0 ] && [ -f "$copy.tmp/outils-arretes" ]; then
    while read -r stack; do
      docker compose --project-directory "$stack" start >/dev/null 2>&1
    done < "$copy.tmp/outils-arretes"
  fi
  [ "$code" = 0 ] || rm -rf "$copy.tmp"
  [ "$reopened" = 1 ] && mount -o remount,ro "$disk" 2>/dev/null
  printf '{"start":%s,"end":%s,"code":%s,"step":"%s","needed":%s,"free":%s}\n' \
    "$started" "$(date +%s)" "$code" "$step" "$needed" "$free" > "$state_file"
}
trap finish EXIT
mountpoint -q "$disk" || exit 10
[ "$(findmnt -no FSTYPE "$disk")" = vfat ] && exit 16
case ",$(findmnt -no OPTIONS "$disk")," in
  *,ro,*) mount -o remount,rw "$disk" || exit 11; reopened=1 ;;
esac
step=place
rm -rf "$copy" "$copy.tmp"
needed=$(du -sbx --exclude='adresse-*' --exclude=adresses /opt/stacks /opt/dockge/data 2>/dev/null | awk '{s+=$1} END {print s+0}')
if [ "$with_files" = 1 ]; then
  needed=$((needed + $(du -sbx --exclude=Restaurations /srv/data | cut -f1)))
fi
free=$(df -B1 --output=avail "$disk" | tail -1 | tr -d ' ')
[ "$needed" -lt "$free" ] || exit 12
mkdir -p "$copy.tmp" || exit 11
step=arret
for stack in $(docker ps --format '{{.Label "com.docker.compose.project.working_dir"}}' | sort -u); do
  case "$stack" in /opt/stacks/jabba-app|/opt/dockge|"") continue ;; esac
  echo "$stack" >> "$copy.tmp/outils-arretes"
  docker compose --project-directory "$stack" stop >/dev/null 2>&1 || exit 13
done
step=outils
what=(stacks)
[ -d /opt/dockge/data ] && what+=(dockge/data)
tar -C /opt --numeric-owner --xattrs --acls --one-file-system \
  --exclude='stacks/adresse-*' --exclude=stacks/adresses -cpf "$copy.tmp/outils.tar" "${what[@]}"
[ $? -le 1 ] || exit 14
crontab -l > "$copy.tmp/crontab" 2>/dev/null || true
tailscale serve status --json > "$copy.tmp/tailscale-serve.json" 2>/dev/null || true
if [ "$with_files" = 1 ]; then
  step=fichiers
  tar -C /srv --numeric-owner --xattrs --acls --one-file-system --exclude='data/.demenagement-jabba*' \
    --exclude=data/dossiers/Restaurations -cpf "$copy.tmp/fichiers.tar" data
  [ $? -le 1 ] || exit 15
fi
files_json=false
[ "$with_files" = 1 ] && files_json=true
printf '{"version":"%s","date":%s,"serveur":"%s","compte":"%s","fichiers":%s}\n' \
  "$version" "$started" "$(hostname)" "$(getent passwd 1000 | cut -d: -f1)" "$files_json" > "$copy.tmp/manifeste.json"
mv "$copy.tmp" "$copy" && sync
step=fini
