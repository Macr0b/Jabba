#!/bin/sh
# Le mot de passe du compte du serveur : $1 le geste (« lire » : sa ligne de /etc/shadow ;
# « changer » : « compte:mot de passe » arrive sur l'entrée standard), $2 le compte. Seulement un
# compte de personne (numéro 1000 à 59999) : jamais root ni un compte de service.
uid=$(id -u "$2" 2>/dev/null) && [ "$uid" -ge 1000 ] && [ "$uid" -lt 60000 ] || {
  echo "Compte refusé" >&2
  exit 5
}
case "$1" in
  lire) exec getent shadow "$2" ;;
  changer) exec chpasswd ;;
esac
exit 6
