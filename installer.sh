#!/bin/bash
# Installation automatique d'un serveur Jabba.
#
# À lancer sur un Debian 13 tout juste installé, depuis le compte créé à
# l'installation (celui qui deviendra le compte du serveur) :
#
#   curl -fsSL https://github.com/Macr0b/Jabba/raw/main/installer.sh -o installer.sh && sudo bash installer.sh
#
# Il fait tout le socle de la méthode Jabba, puis installe l'appli :
#   boîte noire, mises à jour automatiques, réseau qui se rétablit tout seul,
#   capot et écran (portable), batterie, disque de données, Tailscale, Docker,
#   SSH par clé seulement, Dockge, appli Jabba.
# On peut le relancer sans risque : ce qui est déjà fait est laissé tel quel.
# Tout ce qu'il fait est noté dans /var/log/jabba-installation.log.

set -uo pipefail

DEPOT="Macr0b/Jabba"
JOURNAL=/var/log/jabba-installation.log
ETAPES=12
n=0

# ------------------------------------------------------------ affichage
gras=$'\e[1m'; vert=$'\e[32m'; jaune=$'\e[33m'; rouge=$'\e[31m'; gris=$'\e[90m'; fin=$'\e[0m'
etape() { n=$((n + 1)); echo; echo "${gras}[$n/$ETAPES] $*${fin}"; echo "== [$n/$ETAPES] $*" >> "$JOURNAL"; }
ok()    { echo "  ${vert}✓${fin} $*"; echo "   ok : $*" >> "$JOURNAL"; }
info()  { echo "  ${gris}$*${fin}"; }
note()  { echo "  ${jaune}!${fin} $*"; echo "   note : $*" >> "$JOURNAL"; A_FAIRE+=("$*"); }
echec() { echo; echo "  ${rouge}✗ $*${fin}"; echo "   ÉCHEC : $*" >> "$JOURNAL";
          echo "  Le détail est dans $JOURNAL. Corrige, puis relance : sudo bash installer.sh"; exit 1; }
lancer() { "$@" >> "$JOURNAL" 2>&1; }   # une commande, sa sortie va au journal
A_FAIRE=()

# ------------------------------------------------------------ vérifications
[ "$(id -u)" = 0 ] || { echo "Lance-le avec sudo : sudo bash installer.sh"; exit 1; }
MOI="${SUDO_USER:-}"
if [ -z "$MOI" ] || [ "$MOI" = root ]; then
  echo "Lance-le depuis ton compte (pas depuis root) : sudo bash installer.sh"; exit 1
fi
MAISON=$(getent passwd "$MOI" | cut -d: -f6)
. /etc/os-release
[ "${ID:-}" = debian ] || { echo "Ce script est prévu pour Debian."; exit 1; }
[ "${VERSION_ID:-}" = 13 ] || echo "${jaune}Attention : prévu pour Debian 13, tu as Debian ${VERSION_ID:-?}.${fin}"
SERVEUR=$(hostname)
: > "$JOURNAL"; chmod 600 "$JOURNAL"
echo "Installation Jabba du $(date '+%F %T'), serveur $SERVEUR, compte $MOI" >> "$JOURNAL"

echo "${gras}Installation de Jabba sur « $SERVEUR »${fin}"
echo "Compte du serveur : ${gras}$MOI${fin} (c'est aussi le super admin de l'appli)."
echo "Compte 15 à 30 minutes. Deux moments te demanderont quelque chose : le disque de données, et la connexion à Tailscale."

ping -c 1 -W 3 deb.debian.org >/dev/null 2>&1 || echec "Pas d'accès à Internet. Vérifie le câble réseau, puis relance."

# ------------------------------------------------------------ 1. outils de base
etape "Outils de base et mises à jour"
export DEBIAN_FRONTEND=noninteractive
lancer apt-get update || echec "apt-get update a échoué."
lancer apt-get full-upgrade -y || echec "La mise à jour du système a échoué."
lancer apt-get install -y curl git htop nano jq unattended-upgrades apt-listchanges avahi-daemon ca-certificates parted restic \
  || echec "Installation des outils de base impossible."
ok "Système à jour, outils installés"

# ------------------------------------------------------------ 2. boîte noire
etape "Boîte noire (tout ce qui arrive au serveur est noté)"
mkdir -p /etc/systemd/journald.conf.d /var/log/journal /var/log/boite-noire
printf '[Journal]\nStorage=persistent\nSystemMaxUse=500M\n' > /etc/systemd/journald.conf.d/boite-noire.conf
lancer systemctl restart systemd-journald
lancer apt-get install -y etckeeper || echec "Installation d'etckeeper impossible."
cat > /usr/local/sbin/sonde-reseau <<'EOF'
#!/bin/sh
# Une ligne par minute : carte, liaison, adresse, box, Internet, noms.
mesure() {
  r=$(ping -n -q -c 5 -i 0.2 -W 1 "$1" 2>/dev/null)
  perte=$(echo "$r" | grep -o '[0-9.]*% packet loss' | cut -d% -f1)
  moy=$(echo "$r" | awk -F/ '/^rtt/{print $5}')
  echo "${perte:-100}% perdus ${moy:--} ms"
}
box=$(ip -4 route show default | awk '{print $3; exit}')
for carte in $(ls /sys/class/net | grep -E '^(en|wl|eth)'); do
  etat=$(cat /sys/class/net/$carte/operstate 2>/dev/null)
  vitesse=$(cat /sys/class/net/$carte/speed 2>/dev/null)
  adresse=$(ip -4 -br addr show dev "$carte" 2>/dev/null | awk '{print $3}')
  echo "$(date '+%F %T') | $carte | $etat ${vitesse:-?} | ${adresse:-sans adresse}"
done
if [ -n "$box" ]; then
  echo "$(date '+%F %T') | box $box : $(mesure "$box") | Internet : $(mesure 1.1.1.1) | noms : $(getent hosts deb.debian.org >/dev/null && echo ok || echo KO)"
else
  echo "$(date '+%F %T') | aucune route vers la box"
fi
EOF
chmod 755 /usr/local/sbin/sonde-reseau
cat > /etc/systemd/system/sonde-reseau.service <<'EOF'
[Unit]
Description=Boîte noire : sonde réseau

[Service]
Type=oneshot
ExecStart=/bin/sh -c '/usr/local/sbin/sonde-reseau >> /var/log/boite-noire/reseau.log'
EOF
cat > /etc/systemd/system/sonde-reseau.timer <<'EOF'
[Unit]
Description=Boîte noire : sonde réseau chaque minute

[Timer]
OnBootSec=20s
OnUnitActiveSec=1min
AccuracySec=5s

[Install]
WantedBy=timers.target
EOF
cat > /etc/logrotate.d/boite-noire <<'EOF'
/var/log/boite-noire/*.log {
  weekly
  rotate 8
  compress
  missingok
  notifempty
}
EOF
cat > /usr/local/sbin/boite-noire <<'EOF'
#!/bin/sh
echo "== Démarrages (le dernier en bas)"
journalctl --list-boots --no-pager | tail -5
echo; echo "== Arrêts et redémarrages demandés"
journalctl -q --no-pager -n 5 -g 'reboot|poweroff|shutdown|System is (rebooting|powering)' -u systemd-logind 2>/dev/null | tail -5
echo; echo "== Réseau (dernières mesures)"
tail -n 12 /var/log/boite-noire/reseau.log 2>/dev/null
echo; echo "== Connexions"
journalctl -q --no-pager -n 200 -u ssh -u systemd-logind | grep -E 'Accepted|New session' | tail -5
echo; echo "== Commandes sudo"
journalctl -q --no-pager -n 500 _COMM=sudo | grep COMMAND | tail -8 | sed 's/.*sudo\[[0-9]*\]: *//'
echo; echo "== Réglages modifiés (/etc)"
git -C /etc log --date=format:'%F %R' --pretty='%ad  %s' -8 2>/dev/null
echo; echo "== Logiciels installés ou mis à jour"
grep -hE ' (install|upgrade) ' /var/log/dpkg.log 2>/dev/null | tail -8 | awk '{print $1, $2, $3, $4}'
EOF
chmod 755 /usr/local/sbin/boite-noire
lancer systemctl daemon-reload
lancer systemctl enable --now sonde-reseau.timer
usermod -aG adm,systemd-journal "$MOI"
ok "Journal conservé, réglages historisés, sonde réseau chaque minute (commande : sudo boite-noire)"

# ------------------------------------------------------------ 3. réseau
etape "Réseau qui se rétablit tout seul"
printf '[Match]\nName=en*\n\n[Network]\nDHCP=yes\n' > /etc/systemd/network/lan.network
sed -i -E 's/^(allow-hotplug|auto|iface) (en|eth)/#&/' /etc/network/interfaces 2>/dev/null || true
lancer systemctl enable systemd-networkd
lancer apt-get install -y systemd-resolved || echec "Installation de systemd-resolved impossible."
ok "Toute carte réseau filaire est prise en charge, et l'adresse est redemandée au retour du câble (actif au redémarrage)"

# ------------------------------------------------------------ 4. mises à jour automatiques
etape "Mises à jour automatiques"
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/20auto-upgrades
cat > /etc/apt/apt.conf.d/52auto-serveur <<'EOF'
Unattended-Upgrade::Origins-Pattern {
  "origin=Debian,codename=${distro_codename}-updates";
  "origin=Tailscale";
  "origin=Docker";
};
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF
ok "Correctifs installés chaque nuit, redémarrage à 4 h si nécessaire"

# ------------------------------------------------------------ 5. portable : capot, écran, batterie
etape "Réglages de la machine"
if ls /proc/acpi/button/lid/* >/dev/null 2>&1 || [ -d /sys/class/power_supply/BAT0 ] || [ -d /sys/class/power_supply/BAT1 ]; then
  mkdir -p /etc/systemd/logind.conf.d
  printf '[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\nHandleLidSwitchDocked=ignore\n' > /etc/systemd/logind.conf.d/capot.conf
  ok "Portable : le serveur continue de tourner capot fermé"
fi
if ! grep -q consoleblank /etc/default/grub; then
  sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT="\(.*\)"/GRUB_CMDLINE_LINUX_DEFAULT="\1 consoleblank=60"/' /etc/default/grub
  lancer update-grub
fi
ok "L'écran s'éteint seul après 1 minute"
# Disques à plateaux : ils s'arrêtent après 20 minutes sans servir (moins de courant, moins d'usure).
# Le premier accès suivant prend 3 à 5 secondes, le temps qu'ils redémarrent.
lancer apt-get install -y hdparm
printf '%s\n' '# Jabba : un disque à plateaux s'"'"'arrête après 20 minutes sans servir.' \
  'ACTION=="add|change", SUBSYSTEM=="block", KERNEL=="sd[a-z]", ATTR{queue/rotational}=="1", RUN+="/usr/sbin/hdparm -B 127 -S 240 /dev/%k"' \
  > /etc/udev/rules.d/69-jabba-disques.rules
lancer udevadm control --reload
lancer udevadm trigger --subsystem-match=block --action=change
ok "Disques à plateaux : arrêt après 20 minutes sans servir"
CONSERVATION=$(ls /sys/bus/platform/drivers/ideapad_acpi/*/conservation_mode 2>/dev/null | head -1)
if [ -n "$CONSERVATION" ]; then
  echo 1 > "$CONSERVATION" 2>/dev/null || true
  echo "w $CONSERVATION - - - - 1" > /etc/tmpfiles.d/batterie.conf
  ok "Batterie protégée (charge limitée pour durer)"
fi

# ------------------------------------------------------------ 6. disque de données
etape "Disque de données"
mkdir -p /srv/data /srv/disques
if findmnt -n /srv/data >/dev/null; then
  ok "Déjà monté : $(findmnt -n -o SOURCE /srv/data) sur /srv/data"
else
  SYSTEME=$(lsblk -no PKNAME "$(findmnt -n -o SOURCE /)" | head -1)
  mapfile -t DISQUES < <(lsblk -dnpo NAME,TYPE,TRAN | awk '$2=="disk" && $3!="usb"{print $1}' | grep -v "/dev/$SYSTEME$")
  if [ ${#DISQUES[@]} -eq 0 ]; then
    note "Pas de deuxième disque interne : les données iront sur le disque système (/srv/data)."
  else
    echo "  Disques disponibles (le disque du système, $SYSTEME, n'est pas proposé) :"
    i=1
    for d in "${DISQUES[@]}"; do
      echo "    $i) $d — $(lsblk -dno SIZE,MODEL "$d" | xargs) — contient : $(lsblk -no FSTYPE,LABEL "$d" | xargs | sed 's/^$/rien/')"
      i=$((i + 1))
    done
    echo "    0) aucun (les données restent sur le disque système)"
    read -rp "  Numéro du disque de données : " choix </dev/tty
    if [[ "$choix" =~ ^[1-9][0-9]*$ ]] && [ "$choix" -le ${#DISQUES[@]} ]; then
      D=${DISQUES[$((choix - 1))]}
      PART=$(lsblk -lnpo NAME,FSTYPE "$D" | awk '$2=="ext4"{print $1; exit}')
      if [ -n "$PART" ]; then
        read -rp "  $PART contient déjà des données (ext4). Le garder tel quel, sans rien effacer ? (o/n) " garder </dev/tty
      fi
      if [ -z "$PART" ] || [ "${garder:-n}" != o ]; then
        echo "  ${rouge}Tout le contenu de $D ($(lsblk -dno SIZE,MODEL "$D" | xargs)) va être effacé.${fin}"
        read -rp "  Pour confirmer, tape EFFACER : " conf </dev/tty
        [ "$conf" = EFFACER ] || echec "Disque non confirmé. Rien n'a été effacé."
        lancer parted "$D" --script mklabel gpt mkpart data ext4 0% 100% || echec "Partitionnement impossible."
        sleep 2
        PART=$(lsblk -lnpo NAME,TYPE "$D" | awk '$2=="part"{print $1; exit}')
        lancer mkfs.ext4 -F -L data "$PART" || echec "Formatage impossible."
        ok "$D préparé"
      fi
      UUID=$(blkid -s UUID -o value "$PART")
      grep -q "$UUID" /etc/fstab || echo "UUID=$UUID /srv/data ext4 defaults,nofail 0 2" >> /etc/fstab
      lancer systemctl daemon-reload
      mount /srv/data || echec "Montage de /srv/data impossible."
      ok "$PART monté sur /srv/data, à chaque démarrage"
    else
      note "Aucun disque choisi : les données restent sur le disque système."
    fi
  fi
fi
mkdir -p /srv/data/media /srv/data/downloads /srv/data/dossiers
chown "$MOI:$MOI" /srv/data /srv/data/media /srv/data/downloads /srv/data/dossiers
ok "Dossiers de données prêts (/srv/data), disques externes dans /srv/disques"

# ------------------------------------------------------------ 7. Tailscale
etape "Tailscale (l'accès au serveur de partout)"
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | lancer sh || echec "Installation de Tailscale impossible."
fi
if ! tailscale status >/dev/null 2>&1; then
  echo "  Un lien va s'afficher : ouvre-le sur ton ordinateur ou ton téléphone et connecte-toi à ton compte Tailscale."
  tailscale up </dev/tty || echec "Connexion à Tailscale non faite."
fi
TS_LOGIN=$(tailscale status --json | jq -r '.User[(.Self.UserID|tostring)].LoginName // empty')
TS_NOM=$(tailscale status --json | jq -r '.Self.DNSName' | sed 's/\.$//')
[ -n "$TS_LOGIN" ] || echec "Compte Tailscale introuvable."
ok "Connecté : $TS_NOM (compte $TS_LOGIN)"
if [ -n "$(tailscale status --json | jq -r '.Self.KeyExpiry // empty')" ]; then
  note "Dans la console Tailscale (login.tailscale.com/admin/machines) : sur la ligne $SERVEUR, … → Disable key expiry. Sinon le serveur se déconnecte dans quelques mois."
else
  ok "Clé Tailscale sans date d'expiration"
fi

# ------------------------------------------------------------ 8. Docker
etape "Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | lancer sh || echec "Installation de Docker impossible."
fi
usermod -aG docker "$MOI"
docker compose version >/dev/null 2>&1 || echec "Docker Compose absent."
ok "Docker $(docker --version | awk '{print $3}' | tr -d ,) installé"

# ------------------------------------------------------------ 9. SSH par clé seulement
etape "SSH par clé seulement"
if [ -s "$MAISON/.ssh/authorized_keys" ]; then
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n' > /etc/ssh/sshd_config.d/securite.conf
  if sshd -t 2>>"$JOURNAL"; then
    lancer systemctl reload ssh
    ok "Connexion SSH par clé seulement ($(grep -c . "$MAISON/.ssh/authorized_keys") clé(s) autorisée(s))"
  else
    rm -f /etc/ssh/sshd_config.d/securite.conf
    note "Réglage SSH refusé : laissé tel quel."
  fi
else
  note "Aucune clé SSH sur le compte $MOI : connexion par mot de passe laissée ouverte. Ajoute ta clé (ssh-copy-id), puis relance le script."
fi

# ------------------------------------------------------------ 10. Dockge
etape "Dockge (gérer les outils depuis le navigateur)"
mkdir -p /opt/stacks /opt/dockge
chown "$MOI:$MOI" /opt/stacks /opt/dockge
if [ ! -f /opt/dockge/compose.yaml ]; then
  curl -fsSL https://dockge.kuma.pet/compose.yaml -o /opt/dockge/compose.yaml || echec "Téléchargement de Dockge impossible."
  chown "$MOI:$MOI" /opt/dockge/compose.yaml
fi
(cd /opt/dockge && lancer docker compose up -d) || echec "Démarrage de Dockge impossible."
ok "Dockge : http://$SERVEUR.local:5001 (à ouvrir une fois pour créer ton compte)"

# ------------------------------------------------------------ 11. l'appli Jabba
etape "Appli Jabba"
APP=/opt/stacks/jabba-app
if [ ! -d "$APP/.git" ]; then
  runuser -u "$MOI" -- git clone --quiet "https://github.com/$DEPOT.git" "$APP" || echec "Téléchargement de l'appli impossible."
fi
cd "$APP" || echec "Dossier $APP introuvable."
runuser -u "$MOI" -- git fetch --quiet --tags
TAG=$(runuser -u "$MOI" -- git describe --tags --abbrev=0 "$(runuser -u "$MOI" -- git rev-list --tags --max-count=1)")
runuser -u "$MOI" -- git -c advice.detachedHead=false checkout --quiet "$TAG" || echec "Version $TAG introuvable."
if [ ! -f .env ]; then
  cat > .env <<EOF
# Réglages de l'appli Jabba sur ce serveur (créés par le script d'installation).
TS_USERS=$TS_LOGIN
ADMIN_USER=$MOI
JABBA_DEPOT=$DEPOT
LIENS=dockge=http://$SERVEUR.local:5001
SEUIL_BATTERIE=15
EOF
  chown "$MOI:$MOI" .env; chmod 600 .env
fi
# Lancée par systemd : la construction va au bout même si ce script s'arrête en route
# (par exemple s'il tourne dans le terminal de l'appli, qui redémarre à ce moment-là).
info "Construction de l'appli : 3 à 10 minutes."
systemd-run --wait --collect --quiet --unit="jabba-installation-$(date +%s)" -p WorkingDirectory="$APP" \
  docker compose up -d --build >> "$JOURNAL" 2>&1 || echec "Construction de l'appli impossible (détails : journalctl -u 'jabba-installation-*')."
if lancer tailscale serve --bg --https=9000 http://127.0.0.1:8090; then
  ok "Jabba $TAG installé : https://$TS_NOM:9000"
else
  note "Jabba tourne, mais l'adresse HTTPS n'a pas pu être créée : active « HTTPS Certificates » dans la console Tailscale (DNS), puis lance : sudo tailscale serve --bg --https=9000 http://127.0.0.1:8090"
fi

# ------------------------------------------------------------ 12. fin
etape "Vérification"
sleep 5
docker ps --format '{{.Names}}' | grep -q jabba-app && ok "L'appli tourne" || note "L'appli ne tourne pas : docker logs jabba-app"
findmnt -n /srv/data >/dev/null && ok "Données : $(df -h --output=avail /srv/data | tail -1 | xargs) libres sur /srv/data"
etckeeper commit "Installation Jabba" >/dev/null 2>&1 || true

echo
echo "${gras}${vert}C'est installé.${fin}"
echo
echo "  ${gras}Appli Jabba${fin} : https://$TS_NOM:9000"
echo "  Identifiant : ${gras}$SERVEUR${fin} · mot de passe : celui du compte $MOI"
echo "  Commande utile : sudo boite-noire (ce qui s'est passé sur le serveur)"
if [ ${#A_FAIRE[@]} -gt 0 ]; then
  echo
  echo "  ${gras}À faire de ton côté :${fin}"
  for a in "${A_FAIRE[@]}"; do echo "   • $a"; done
fi
echo
read -rp "Le serveur doit redémarrer pour finir (réseau, écran). Redémarrer maintenant ? (o/n) " r </dev/tty
if [ "$r" = o ]; then
  echo "Redémarrage… reconnecte-toi dans 2 minutes."
  systemctl reboot
else
  echo "Pense à redémarrer : sudo reboot"
fi
