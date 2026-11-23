"""Outils à activer en option depuis l'Établi : leurs fichiers d'installation, fixés ici.

Le service privilégié (host/actions.py, « installer_outil_option ») ne sait installer que ces
outils-là, avec ces fichiers-là : l'appli ne lui envoie que le nom de l'outil (et, pour Time
Machine, le mot de passe choisi par l'admin, sur l'entrée standard).
Les données (photos, mots de passe, sauvegardes) restent quand on retire un outil.

Sommaire :
  OPTIONAL_TOOLS : pour chaque outil, son nom, son rôle, la mémoire qu'il prend, ses ports et ses fichiers
  SECRET_MARK / PASSWORD_MARK / TAILSCALE_MARK… : remplacés à l'installation (secrets tirés au sort,
                  mot de passe tiré par Jabba, adresse Tailscale du serveur, compte du serveur…)
"""

SECRET_MARK = "@SECRET_TIRE_AU_SORT@"
PASSWORD_MARK = "@MOT_DE_PASSE_ADMIN@"
TAILSCALE_MARK = "@ADRESSE_TAILSCALE@"
LAN_IP_MARK = "@IP_LOCALE@"
TAILSCALE_IP_MARK = "@IP_TAILSCALE@"
SECOND_SECRET_MARK = "@AUTRE_SECRET@"
SERVER_USER_MARK = "@COMPTE_SERVEUR@"  # le premier compte humain du serveur (uid 1000)
SERVER_UID_MARK = "@UID_SERVEUR@"
SERVER_GID_MARK = "@GID_SERVEUR@"
RENDER_GID_MARK = "@GID_RENDER@"  # le groupe qui donne accès à la puce graphique
HOSTNAME_MARK = "@NOM_SERVEUR@"
TAILNET_MARK = "@SUFFIXE_TAILSCALE@"  # tailxxxx.ts.net

VAULTWARDEN_COMPOSE = """services:
  vaultwarden:
    image: vaultwarden/server:latest
    container_name: vaultwarden
    restart: unless-stopped
    ports:
      - "127.0.0.1:8222:80"
    environment:
      # Joignable seulement par Tailscale (adresse https du serveur, port 8222).
      SIGNUPS_ALLOWED: "true"
      SHOW_PASSWORD_HINT: "false"
    volumes:
      - ./donnees:/data
"""

# Le fichier officiel d'Immich (version 3), avec ses ports limités au serveur lui-même.
IMMICH_COMPOSE = """name: immich

services:
  immich-server:
    container_name: immich_server
    image: ghcr.io/immich-app/immich-server:${IMMICH_VERSION:-release}
    volumes:
      - ${UPLOAD_LOCATION}:/data
      - /etc/localtime:/etc/localtime:ro
    env_file:
      - .env
    ports:
      - "127.0.0.1:2283:2283"
    depends_on:
      - redis
      - database
    restart: always
    healthcheck:
      disable: false

  immich-machine-learning:
    container_name: immich_machine_learning
    image: ghcr.io/immich-app/immich-machine-learning:${IMMICH_VERSION:-release}
    volumes:
      - model-cache:/cache
    env_file:
      - .env
    environment:
      MACHINE_LEARNING_MODEL_REVISION: v2
    restart: always
    healthcheck:
      disable: false

  redis:
    container_name: immich_redis
    image: docker.io/valkey/valkey:9@sha256:418652cfb58ef879d4978c33553735d7147016032d5aefaa14c828e611eb9dfd
    healthcheck:
      test: redis-cli ping | grep -q PONG || exit 1
    restart: always

  database:
    container_name: immich_postgres
    image: ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23
    environment:
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_USER: ${DB_USERNAME}
      POSTGRES_DB: ${DB_DATABASE_NAME}
      POSTGRES_INITDB_ARGS: "--data-checksums"
    volumes:
      - ${DB_DATA_LOCATION}:/var/lib/postgresql/data
    shm_size: 128mb
    restart: always
    healthcheck:
      disable: false

volumes:
  model-cache:
"""

IMMICH_ENV = f"""# Les photos vont sur le disque de données ; la base reste sur le SSD (plus rapide).
UPLOAD_LOCATION=/srv/data/Photos-Immich
DB_DATA_LOCATION=./postgres
IMMICH_VERSION=v3
DB_PASSWORD={SECRET_MARK}
DB_USERNAME=postgres
DB_DATABASE_NAME=immich
"""

# Time Machine par le partage de fichiers Mac (SMB) ; le serveur s'annonce sur le réseau (Bonjour).
TIME_MACHINE_COMPOSE = """services:
  time-machine:
    image: mbentley/timemachine:smb
    container_name: time-machine
    restart: unless-stopped
    network_mode: host
    env_file:
      - .env
    volumes:
      - /srv/data/TimeMachine:/opt/timemachine
      - ./etat:/var/lib/samba
"""

TIME_MACHINE_ENV = f"""TM_USERNAME=timemachine
TM_UID=1000
TM_GID=1000
PASSWORD='{PASSWORD_MARK}'
SET_PERMISSIONS=false
VOLUME_SIZE_LIMIT=0
"""

# Bloqueur de publicités et de traqueurs pour tout le réseau (serveur DNS). La page d'admin (port 3000)
# ne s'ouvre que par Tailscale ; le DNS (port 53) répond sur l'adresse de la maison et sur celle de Tailscale,
# pas sur 0.0.0.0 : le résolveur de Debian (127.0.0.53) occupe déjà le port 53 du serveur lui-même.
ADGUARD_COMPOSE = """services:
  adguard-home:
    image: adguard/adguardhome:latest
    container_name: adguard-home
    restart: unless-stopped
    ports:
      - "${IP_LOCALE}:53:53/tcp"
      - "${IP_LOCALE}:53:53/udp"
      - "${IP_TAILSCALE}:53:53/tcp"
      - "${IP_TAILSCALE}:53:53/udp"
      - "127.0.0.1:3000:3000/tcp"
    volumes:
      - ./travail:/opt/adguardhome/work
      - ./reglages:/opt/adguardhome/conf
"""

ADGUARD_ENV = f"""IP_LOCALE={LAN_IP_MARK}
IP_TAILSCALE={TAILSCALE_IP_MARK}
"""

# Au démarrage, Tailscale peut arriver après Docker : on autorise à réserver une adresse pas encore présente.
NONLOCAL_BIND = (
    "sysctl -qw net.ipv4.ip_nonlocal_bind=1 && echo net.ipv4.ip_nonlocal_bind=1 > /etc/sysctl.d/90-jabba-adguard.conf"
)

# Papiers numérisés : on dépose dans Téléchargements › Paperless-a-classer (visible dans Fichiers et
# Nextcloud, qui y écrivent en groupe www-data) ; Paperless lit le texte, range, puis retire le fichier.
PAPERLESS_COMPOSE = """services:
  broker:
    image: docker.io/valkey/valkey:9-alpine
    restart: unless-stopped
    volumes:
      - ./redis:/data
  db:
    image: docker.io/library/postgres:18
    restart: unless-stopped
    volumes:
      - ./postgres:/var/lib/postgresql
    environment:
      POSTGRES_DB: paperless
      POSTGRES_USER: paperless
      POSTGRES_PASSWORD: ${DB_PASSWORD}
  webserver:
    image: ghcr.io/paperless-ngx/paperless-ngx:latest
    container_name: paperless
    restart: unless-stopped
    depends_on:
      - db
      - broker
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - ./donnees:/usr/src/paperless/data
      - /srv/data/Paperless/Documents:/usr/src/paperless/media
      - ./export:/usr/src/paperless/export
      - /srv/data/downloads/Paperless-a-classer:/usr/src/paperless/consume
    env_file: .env
    environment:
      PAPERLESS_REDIS: redis://broker:6379
      PAPERLESS_DBHOST: db
      PAPERLESS_DBENGINE: postgresql
      PAPERLESS_DBPASS: ${DB_PASSWORD}
"""

PAPERLESS_ENV = f"""DB_PASSWORD={SECRET_MARK}
PAPERLESS_SECRET_KEY={SECRET_MARK}
PAPERLESS_URL=https://{TAILSCALE_MARK}:8000
PAPERLESS_OCR_LANGUAGE=fra+eng
PAPERLESS_TIME_ZONE=Europe/Paris
PAPERLESS_ADMIN_USER=admin
PAPERLESS_ADMIN_PASSWORD='{PASSWORD_MARK}'
USERMAP_UID=1000
USERMAP_GID=1000
"""

# Inventaire (bois, visserie, consommables, outils prêtés), avec étiquettes à QR code.
HOMEBOX_COMPOSE = """services:
  homebox:
    image: ghcr.io/sysadminsmedia/homebox:latest
    container_name: homebox
    restart: unless-stopped
    ports:
      - "127.0.0.1:7745:7745"
    env_file: .env
    environment:
      HBOX_OPTIONS_ALLOW_ANALYTICS: "false"
    volumes:
      - ./donnees:/data
"""

HOMEBOX_ENV = f"""# Ouvert pour créer le premier compte ; les suivants s'invitent depuis Homebox.
HBOX_OPTIONS_ALLOW_REGISTRATION=true
HBOX_AUTH_API_KEY_PEPPER={SECRET_MARK}
"""

# Surveille des pages web (prix, retour en stock, notice mise à jour) et prévient quand elles changent.
# Sur le réseau de l'hôte, mais n'écoute que 127.0.0.1 : seul le serveur lui-même le joint.
CHANGEDETECTION_COMPOSE = """services:
  changedetection:
    image: ghcr.io/dgtlmoon/changedetection.io:latest
    container_name: changedetection
    restart: unless-stopped
    network_mode: host
    env_file: .env
    environment:
      LISTEN_HOST: 127.0.0.1
      PORT: "5005"
      TZ: Europe/Paris
    volumes:
      - ./donnees:/datastore
"""

CHANGEDETECTION_ENV = f"""BASE_URL=https://{TAILSCALE_MARK}:5005
"""

# Les dossiers de données communs à Nextcloud et Jellyfin (guide, ancienne étape 8.1) : films, séries,
# téléchargements ; Nextcloud (www-data, 33) écrit dans les films et les téléchargements.
DATA_FOLDERS = (
    "mkdir -p /srv/data/media/films /srv/data/media/series /srv/data/downloads /srv/data/nextcloud /srv/data/dossiers"
    " && chown 1000:1000 /srv/data /srv/data/media /srv/data/media/films /srv/data/media/series"
    " /srv/data/downloads /srv/data/dossiers"
    " && chown 33:33 /srv/data/nextcloud"
    " && chgrp -R 33 /srv/data/media /srv/data/downloads"
    " && chmod -R g+rwX /srv/data/media /srv/data/downloads"
    " && find /srv/data/media /srv/data/downloads -type d -exec chmod g+s {} +"
)

JELLYFIN_COMPOSE = """services:
  jellyfin:
    image: jellyfin/jellyfin:latest
    container_name: jellyfin
    user: "${PUID}:${PGID}"
    group_add: ["${RENDER_GID}"]
    devices: ["/dev/dri:/dev/dri"]
    volumes:
      - ./config:/config
      - ./cache:/cache
      - /srv/data:/data:ro
      # Tous les disques externes, branchés ou non
      - type: bind
        source: /srv/disques
        target: /disques
        read_only: true
        bind:
          propagation: rslave
    ports:
      - "8096:8096"
    restart: unless-stopped
"""
JELLYFIN_ENV = f"""TZ=Europe/Paris
PUID={SERVER_UID_MARK}
PGID={SERVER_GID_MARK}
RENDER_GID={RENDER_GID_MARK}
"""

NEXTCLOUD_COMPOSE = """services:
  nextcloud-db:
    image: mariadb:11
    container_name: nextcloud-db
    command: --transaction-isolation=READ-COMMITTED
    environment:
      - MARIADB_ROOT_PASSWORD=${DB_ROOT_PASSWORD}
      - MARIADB_DATABASE=nextcloud
      - MARIADB_USER=nextcloud
      - MARIADB_PASSWORD=${DB_PASSWORD}
    volumes:
      - ./db:/var/lib/mysql
    restart: unless-stopped

  nextcloud-redis:
    image: redis:alpine
    container_name: nextcloud-redis
    restart: unless-stopped

  nextcloud:
    image: nextcloud:stable
    container_name: nextcloud
    depends_on: [nextcloud-db, nextcloud-redis]
    environment:
      - MYSQL_HOST=nextcloud-db
      - MYSQL_DATABASE=nextcloud
      - MYSQL_USER=nextcloud
      - MYSQL_PASSWORD=${DB_PASSWORD}
      - REDIS_HOST=nextcloud-redis
      - NEXTCLOUD_ADMIN_USER=${NC_ADMIN_USER}
      - NEXTCLOUD_ADMIN_PASSWORD=${NC_ADMIN_PASSWORD}
      - NEXTCLOUD_TRUSTED_DOMAINS=${NC_DOMAINS}
    volumes:
      - ./html:/var/www/html
      - /srv/data/nextcloud:/var/www/html/data
      # Films et téléchargements, visibles dans Nextcloud
      - /srv/data/media:/mnt/media
      - /srv/data/downloads:/mnt/downloads
      # Tous les disques externes, branchés ou non
      - type: bind
        source: /srv/disques
        target: /mnt/disques
        bind:
          propagation: rslave
    ports:
      - "8081:80"
    restart: unless-stopped
"""
NEXTCLOUD_ENV = f"""DB_ROOT_PASSWORD={SECRET_MARK}
DB_PASSWORD={SECOND_SECRET_MARK}
NC_ADMIN_USER={SERVER_USER_MARK}
NC_ADMIN_PASSWORD='{PASSWORD_MARK}'
NC_DOMAINS="{HOSTNAME_MARK} {TAILSCALE_MARK} nextcloud.{TAILNET_MARK} {LAN_IP_MARK} {TAILSCALE_IP_MARK}"
"""
# Les tâches de fond de Nextcloud, toutes les 5 minutes (une seule fois dans la table de root).
NEXTCLOUD_CRON = (
    "(crontab -l 2>/dev/null | grep -v 'nextcloud php -f /var/www/html/cron.php';"
    " echo '*/5 * * * * docker exec -u www-data nextcloud php -f /var/www/html/cron.php') | crontab -"
)
# Au retrait : la ligne disparaît (ses données, elles, restent).
NEXTCLOUD_CRON_REMOVE = "crontab -l 2>/dev/null | grep -v 'nextcloud php -f /var/www/html/cron.php' | crontab -"

OPTIONAL_TOOLS = {
    "nextcloud": {
        "nom": "Nextcloud",
        "role": "Fichiers, photos, agenda : ton iCloud à toi, sur Mac, iPhone et navigateur",
        "memoire_go": 1.0,
        "port_https": None,  # son adresse à part : nextcloud.tailxxxx.ts.net (services/tool_addresses.py)
        "port_local": 8081,
        "fichiers": {"compose.yaml": NEXTCLOUD_COMPOSE, ".env": NEXTCLOUD_ENV},
        "preparer": DATA_FOLDERS + " && " + NEXTCLOUD_CRON,
        "nettoyer": NEXTCLOUD_CRON_REMOVE,
        # Le compte de l'admin : celui du serveur, avec un mot de passe tiré par Jabba (Identifiants).
        "mot_de_passe": True,
        "identifiant": SERVER_USER_MARK,
    },
    "jellyfin": {
        "nom": "Jellyfin",
        "role": "Films et séries, sur la télé, l'iPhone et le Mac",
        "memoire_go": 0.5,
        "port_https": None,  # son adresse à part : jellyfin.tailxxxx.ts.net
        "port_local": 8096,
        "fichiers": {"compose.yaml": JELLYFIN_COMPOSE, ".env": JELLYFIN_ENV},
        "preparer": DATA_FOLDERS + " && mkdir -p /opt/stacks/jellyfin/config /opt/stacks/jellyfin/cache"
        " && chown 1000:1000 /opt/stacks/jellyfin/config /opt/stacks/jellyfin/cache",
        "mot_de_passe": True,
        "identifiant": SERVER_USER_MARK,
    },
    "vaultwarden": {
        "nom": "Vaultwarden",
        "role": "Mots de passe (applis Bitwarden sur Mac, iPhone et navigateur)",
        "memoire_go": 0.1,
        "port_https": 8222,
        "port_local": 8222,
        "fichiers": {"compose.yaml": VAULTWARDEN_COMPOSE},
        "preparer": "",
        "mot_de_passe": False,
    },
    "immich": {
        "nom": "Immich",
        "role": "Photos et vidéos de l'iPhone, sauvegardées automatiquement",
        "memoire_go": 3.0,
        "port_https": 2283,
        "port_local": 2283,
        "fichiers": {"compose.yaml": IMMICH_COMPOSE, ".env": IMMICH_ENV},
        "preparer": "mkdir -p /srv/data/Photos-Immich",
        "mot_de_passe": False,
    },
    "time-machine": {
        "nom": "Time Machine",
        "role": "Sauvegarde des Mac par le réseau, sans rien brancher",
        "memoire_go": 0.1,
        "port_https": None,
        "port_local": None,
        "fichiers": {"compose.yaml": TIME_MACHINE_COMPOSE, ".env": TIME_MACHINE_ENV},
        "preparer": "mkdir -p /srv/data/TimeMachine && chown 1000:1000 /srv/data/TimeMachine",
        "mot_de_passe": True,
        "identifiant": "timemachine",
    },
    "adguard-home": {
        "nom": "AdGuard Home",
        "role": "Bloque les publicités et les traqueurs, sur les appareils que tu choisis",
        "memoire_go": 0.1,
        "port_https": 3000,
        "port_local": 3000,
        "fichiers": {"compose.yaml": ADGUARD_COMPOSE, ".env": ADGUARD_ENV},
        "preparer": NONLOCAL_BIND,
        # Tiré au sort et gardé par l'appli : elle fait l'installation d'AdGuard et règle les appareils.
        "mot_de_passe": True,
        "identifiant": "admin",
    },
    "paperless": {
        "nom": "Paperless",
        "role": "Tes papiers numérisés, lus et rangés tout seuls, retrouvés par recherche",
        "memoire_go": 1.5,
        "port_https": 8000,
        "port_local": 8000,
        "fichiers": {"compose.yaml": PAPERLESS_COMPOSE, ".env": PAPERLESS_ENV},
        "preparer": (
            "mkdir -p /srv/data/downloads/Paperless-a-classer /srv/data/Paperless/Documents"
            " && chown 1000:1000 /srv/data/Paperless /srv/data/Paperless/Documents"
            " && chown 1000:33 /srv/data/downloads/Paperless-a-classer"
            " && chmod 2775 /srv/data/downloads/Paperless-a-classer"
        ),
        "mot_de_passe": True,
        "identifiant": "admin",
        # Avec une adresse à part (services/tool_addresses.py), Paperless doit la connaître : sinon il
        # refuse les formulaires venus d'une autre adresse que celle-ci.
        "variable_adresse": "PAPERLESS_URL",
    },
    "changedetection": {
        "nom": "changedetection.io",
        "role": "Prévient quand une page web change : prix, retour en stock, notice mise à jour",
        "memoire_go": 0.2,
        "port_https": 5005,
        "port_local": 5005,
        "fichiers": {"compose.yaml": CHANGEDETECTION_COMPOSE, ".env": CHANGEDETECTION_ENV},
        "preparer": "",
        "mot_de_passe": False,
    },
    "homebox": {
        "nom": "Homebox",
        "role": "L'inventaire : stock, consommables, outils prêtés, étiquettes à QR code",
        "memoire_go": 0.1,
        "port_https": 7745,
        "port_local": 7745,
        "fichiers": {"compose.yaml": HOMEBOX_COMPOSE, ".env": HOMEBOX_ENV},
        "preparer": "",
        "mot_de_passe": False,
    },
}
