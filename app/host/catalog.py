"""Outils à activer en option depuis l'Établi : leurs fichiers d'installation, fixés ici.

Le service privilégié (host/actions.py, « installer_outil_option ») ne sait installer que ces
outils-là, avec ces fichiers-là : l'appli ne lui envoie que le nom de l'outil (et, pour Time
Machine, le mot de passe choisi par l'admin, sur l'entrée standard).
Les données (photos, mots de passe, sauvegardes) restent quand on retire un outil.

Sommaire :
  OPTIONAL_TOOLS : pour chaque outil, son nom, son rôle, la mémoire qu'il prend, ses ports et ses fichiers
  SECRET_MARK / PASSWORD_MARK : remplacés à l'installation (secret tiré au sort / mot de passe de l'admin)
"""

SECRET_MARK = "@SECRET_TIRE_AU_SORT@"
PASSWORD_MARK = "@MOT_DE_PASSE_ADMIN@"

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
    },
}
