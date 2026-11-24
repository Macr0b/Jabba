#!/bin/sh
# Lance une tâche de fond qui va au bout même si l'appli redémarre (host/action_common.py).
# $1 dossier des tâches, $2 nom de l'unité systemd, $3 texte du script ; ensuite : options de
# systemd-run puis l'interpréteur. Le script est posé dans un fichier (en ligne de commande,
# systemd remplacerait les variables) et s'efface dès qu'il démarre.
umask 077
mkdir -p "$1"
printf "%s" "$3" > "$1/$2.sh"
tasks_dir="$1"
unit="$2"
shift 3
exec systemd-run --unit="$unit" --collect "$@" "$tasks_dir/$unit.sh"
