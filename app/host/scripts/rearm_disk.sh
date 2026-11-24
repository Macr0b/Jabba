#!/bin/sh
# Rebranche un disque qui a été débranché sans être éjecté : $1 son dossier. Son ouverture
# automatique (automount) est remise en route, puis on le lit une fois pour l'ouvrir.
ls "$1" >/dev/null 2>&1
U=$(systemd-escape --path --suffix=automount "$1")
systemctl daemon-reload
systemctl reset-failed "${U%.automount}.mount" 2>/dev/null
systemctl restart "$U"
ls "$1" >/dev/null 2>&1
true
