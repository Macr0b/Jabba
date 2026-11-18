"""Outils à activer en option depuis l'Établi : leurs fichiers d'installation, fixés ici.

Le service privilégié (host/actions.py, « installer_outil_option ») ne sait installer que ces
outils-là, avec ces fichiers-là : l'appli ne lui envoie que le nom de l'outil (et, pour Time
Machine, le mot de passe choisi par l'admin, sur l'entrée standard).
Les données (photos, mots de passe, sauvegardes) restent quand on retire un outil.

Sommaire :
  OPTIONAL_TOOLS : pour chaque outil, son nom, son rôle, la mémoire qu'il prend, ses ports et ses fichiers
  SECRET_MARK / PASSWORD_MARK / TAILSCALE_MARK : remplacés à l'installation (secret tiré au sort,
                  mot de passe de l'admin, adresse Tailscale du serveur)
"""

SECRET_MARK = "@SECRET_TIRE_AU_SORT@"
PASSWORD_MARK = "@MOT_DE_PASSE_ADMIN@"
TAILSCALE_MARK = "@ADRESSE_TAILSCALE@"

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
PASSWORD={PASSWORD_MARK}
SET_PERMISSIONS=false
VOLUME_SIZE_LIMIT=0
"""

# Bloqueur de publicités et de traqueurs pour tout le réseau (serveur DNS). La page d'admin (port 3000)
# ne s'ouvre que par Tailscale ; le DNS (port 53) répond aux appareils de la maison.
ADGUARD_COMPOSE = """services:
  adguard-home:
    image: adguard/adguardhome:latest
    container_name: adguard-home
    restart: unless-stopped
    ports:
      - "53:53/tcp"
      - "53:53/udp"
      - "127.0.0.1:3000:3000/tcp"
    volumes:
      - ./travail:/opt/adguardhome/work
      - ./reglages:/opt/adguardhome/conf
"""

# Papiers numérisés : on dépose dans « A-classer » (visible dans Fichiers), Paperless lit le texte et range.
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
      - /srv/data/Paperless/A-classer:/usr/src/paperless/consume
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
PAPERLESS_ADMIN_PASSWORD={PASSWORD_MARK}
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

# Les alertes du serveur sur le téléphone (appli ntfy). L'appli Jabba y publie dans un sujet tiré au sort.
# upstream : pour l'iPhone, ntfy.sh ne reçoit qu'un signal de réveil, jamais le texte de l'alerte.
NTFY_COMPOSE = """services:
  ntfy:
    image: binwiederhier/ntfy:latest
    container_name: ntfy
    restart: unless-stopped
    command: serve
    ports:
      - "127.0.0.1:2586:80"
    env_file: .env
    environment:
      NTFY_BEHIND_PROXY: "true"
      NTFY_CACHE_FILE: /var/lib/ntfy/cache.db
      NTFY_CACHE_DURATION: 72h
      NTFY_UPSTREAM_BASE_URL: https://ntfy.sh
    volumes:
      - ./donnees:/var/lib/ntfy
"""

NTFY_ENV = f"""NTFY_BASE_URL=https://{TAILSCALE_MARK}:2586
"""

# Surveille des pages web (prix, retour en stock, notice mise à jour) et prévient quand elles changent.
# Sur le réseau de l'hôte, mais n'écoute que 127.0.0.1 : il joint ainsi ntfy (127.0.0.1:2586) pour prévenir.
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

OPTIONAL_TOOLS = {
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
        "mot_de_passe_texte": "Mot de passe à taper sur le Mac",
    },
    "adguard-home": {
        "nom": "AdGuard Home",
        "role": "Bloque les publicités et les traqueurs pour tous les appareils de la maison",
        "memoire_go": 0.1,
        "port_https": 3000,
        "port_local": 3000,
        "fichiers": {"compose.yaml": ADGUARD_COMPOSE},
        "preparer": "",
        "mot_de_passe": False,
    },
    "paperless": {
        "nom": "Paperless",
        "role": "Tes papiers numérisés, lus et rangés tout seuls, retrouvés par recherche",
        "memoire_go": 1.5,
        "port_https": 8000,
        "port_local": 8000,
        "fichiers": {"compose.yaml": PAPERLESS_COMPOSE, ".env": PAPERLESS_ENV},
        "preparer": (
            "mkdir -p /srv/data/Paperless/A-classer /srv/data/Paperless/Documents"
            " && chown 1000:1000 /srv/data/Paperless /srv/data/Paperless/A-classer /srv/data/Paperless/Documents"
        ),
        "mot_de_passe": True,
        "mot_de_passe_texte": "Mot de passe du compte « admin » de Paperless",
    },
    "ntfy": {
        "nom": "ntfy",
        "role": "Les alertes du serveur et les rappels d'entretien sur ton téléphone",
        "memoire_go": 0.05,
        "port_https": 2586,
        "port_local": 2586,
        "fichiers": {"compose.yaml": NTFY_COMPOSE, ".env": NTFY_ENV},
        "preparer": "",
        "mot_de_passe": False,
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
