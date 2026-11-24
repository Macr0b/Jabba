#!/bin/bash
# Une sauvegarde complète avec restic (services/backups.py), en tâche de fond sur le serveur.
# Les valeurs arrivent en tête du script, posées par backups.backup_script() :
#   state_file, disk, demo (0 ou 1), sources et excludes (tableaux), keep_daily, keep_weekly,
#   keep_monthly, et RESTIC_REPOSITORY, RESTIC_PASSWORD_FILE, RESTIC_CACHE_DIR (exportées ici).
# Le résultat s'écrit dans state_file : début, fin, code, étape, avertissements, résumé de restic.
# shellcheck disable=SC2154  # sources et excludes : posés en tête par backups.backup_script()
: "${state_file:?}" "${disk:?}" "${demo:?}" "${keep_daily:?}" "${keep_weekly:?}" "${keep_monthly:?}"
export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE RESTIC_CACHE_DIR
set -uo pipefail
started=$(date +%s)
step=disque
reopened=0
warnings=""
summary=null
output=$(mktemp)
# shellcheck disable=SC2329  # appelée par trap
finish() {
  code=$?
  [ "$reopened" = 1 ] && mount -o remount,ro "$disk" 2>/dev/null
  rm -f "$output"
  printf '{"start":%s,"end":%s,"code":%s,"step":"%s","warning":"%s","summary":%s}\n' \
    "$started" "$(date +%s)" "$code" "$step" "$warnings" "$summary" > "$state_file"
}
trap finish EXIT
ls "$disk" >/dev/null 2>&1
if [ "$demo" = 0 ]; then
  mountpoint -q "$disk" || exit 10
  # Un disque en lecture seule n'est ouvert en écriture que le temps de la copie.
  case ",$(findmnt -no OPTIONS "$disk")," in
    *,ro,*) mount -o remount,rw "$disk" || exit 11; reopened=1 ;;
  esac
fi
mkdir -p "$RESTIC_REPOSITORY" || exit 12
step=base
if [ "$demo" = 0 ]; then
  # Copie propre des bases de données (conteneurs *-db), dans le dossier de leur outil.
  for container in $(docker ps --format '{{.Names}}' | grep -- '-db$'); do
    dir=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$container")
    [ -d "$dir" ] || continue
    image=$(docker inspect -f '{{.Config.Image}}' "$container")
    case "$image" in
      *postgres*)
        # shellcheck disable=SC2016  # $POSTGRES_USER : celui du conteneur
        docker exec "$container" sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}"' > "$dir/sauvegarde-base.sql.tmp" ;;
      *mariadb*|*mysql*)
        # shellcheck disable=SC2016  # le mot de passe : celui du conteneur
        docker exec "$container" sh -c 'p="${MARIADB_ROOT_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}"; (command -v mariadb-dump >/dev/null && exec mariadb-dump --all-databases --single-transaction -uroot -p"$p") || exec mysqldump --all-databases --single-transaction -uroot -p"$p"' > "$dir/sauvegarde-base.sql.tmp" ;;
      *) continue ;;
    esac
    if [ -s "$dir/sauvegarde-base.sql.tmp" ]; then
      mv "$dir/sauvegarde-base.sql.tmp" "$dir/sauvegarde-base.sql"
      chmod 600 "$dir/sauvegarde-base.sql"
    else
      rm -f "$dir/sauvegarde-base.sql.tmp"
      warnings="${warnings}Base de $container non copiée. "
    fi
  done
fi
step=init
restic cat config >/dev/null 2>&1 || restic init >/dev/null || exit 13
restic unlock >/dev/null 2>&1
step=copie
exclude_options=()
for pattern in "${excludes[@]}"; do exclude_options+=(--exclude "$pattern"); done
restic backup --tag jabba --exclude-caches "${exclude_options[@]}" "${sources[@]}" --json > "$output"
restic_code=$?
summary=$(grep '"message_type":"summary"' "$output" | tail -1)
[ -n "$summary" ] || summary=null
if [ $restic_code = 3 ]; then
  warnings="${warnings}Quelques fichiers illisibles n'ont pas été copiés. "
elif [ $restic_code != 0 ]; then
  exit 14
fi
step=menage
restic forget --tag jabba --keep-daily "$keep_daily" --keep-weekly "$keep_weekly" --keep-monthly "$keep_monthly" --prune >/dev/null \
  || warnings="${warnings}Le tri des anciennes copies a échoué. "
step=fini
exit 0
