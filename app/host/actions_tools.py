"""Les actions sur les outils : antivirus, accès par le domaine (Caddy), outils en option et leur
adresse à part, conteneurs Docker, et l'administration de Nextcloud (occ).

Chaque action : un nom, des paramètres vérifiés, une commande construite ici (host/actions.py
les rassemble). Sommaire : ACTIONS, le nom de chaque action et sa fonction.
  caddyfile : la configuration de Caddy pour publier un outil à une adresse
  optional_tool : le nom et l'entrée du catalogue d'un outil en option, ou un refus
  optional_password  : le mot de passe demandé par un outil (Time Machine, Paperless) : 15 à 128 caractères, sans apostrophe
  interne : _install_antivirus, _remove_antivirus, _scan_files, _scan_disk, _domain_access, _remove_domain_access, _install_optional, _remove_optional, _address_target, _tool_address, _address_in_tool, _container, _list_containers, _containers_usage, _container_image, _image_details, _container_running, _tool, _nextcloud
"""

import re
import shlex

from host.action_common import ActionError, background, folder_path, host_name, task_unit

# ---------------------------------------------------------------- antivirus (ClamAV), un outil de l'Établi
ANTIVIRUS_FOLDER = "/opt/stacks/antivirus"
# L'image officielle, telle quelle : freshclam tient les signatures à jour et clamd les garde en
# mémoire (environ 1 Go). Jabba lui fait analyser les nouveaux dépôts avec clamdscan.
# /srv est vu en lecture seule ; aucun port ouvert.
ANTIVIRUS_COMPOSE = """services:
  antivirus:
    image: clamav/clamav:stable
    container_name: antivirus
    # Priorité basse : une longue analyse de disque ne ralentit pas le reste du serveur.
    cpu_shares: 256
    volumes:
      - signatures:/var/lib/clamav
      - /srv:/srv:ro
    restart: unless-stopped
volumes:
  signatures:
"""
MAX_SCAN_FILES = 200


def _install_antivirus(args):
    script = (
        f"mkdir -p {ANTIVIRUS_FOLDER} && cat > {ANTIVIRUS_FOLDER}/compose.yaml <<'FIN'\n"
        + ANTIVIRUS_COMPOSE
        + f"FIN\ncd {ANTIVIRUS_FOLDER} && docker compose up -d"
    )
    return ["sh", "-c", script], 1800


def _remove_antivirus(args):
    return ["sh", "-c", f"cd {ANTIVIRUS_FOLDER} && docker compose down -v && rm -rf {ANTIVIRUS_FOLDER}"], 300


def _scan_files(args):
    paths = args.get("chemins") or []
    if not isinstance(paths, list) or not 0 < len(paths) <= MAX_SCAN_FILES:
        raise ActionError("Liste de fichiers refusée")
    checked = [folder_path(path) for path in paths]
    if any(not path.startswith("/srv/") for path in checked):
        raise ActionError("L'antivirus ne voit que /srv")
    # --fdpass : le fichier est ouvert ici et transmis à clamd, qui n'a pas besoin d'avoir le droit de le lire.
    command = ["docker", "exec", "antivirus", "clamdscan", "--no-summary", "--infected", "--fdpass", "--"]
    return command + checked, 1800


def _scan_disk(args):
    """Analyse complète d'un disque, en tâche de fond (des heures pour un gros disque), en priorité basse.
    Le résultat (lignes « chemin: menace FOUND ») se lit ensuite dans le journal de la tâche."""
    path = folder_path(args.get("chemin"))
    if not path.startswith("/srv/"):
        raise ActionError("L'antivirus ne voit que /srv")
    script = "exec docker exec antivirus clamdscan --no-summary --infected --fdpass -- " + shlex.quote(path) + "\n"
    return background(task_unit(args.get("unite")), script, "sh", ("Nice=19", "IOSchedulingClass=idle"))


# ---------------------------------------------------------------- accès par le domaine (Caddy)
# Caddy reçoit Internet (box : port 443 -> serveur : DOMAIN_PORT) et ne sert QUE l'adresse de
# Nextcloud. Jabba n'a pas d'adresse publique. Le certificat HTTPS (Let's Encrypt) est obtenu et
# renouvelé par Caddy, par le port 443 seul (épreuve « TLS-ALPN », sans port 80).
DOMAIN_FOLDER = "/opt/stacks/acces-domaine"
DOMAIN_PORT = 8443
DOMAIN_COMPOSE = """services:
  acces-domaine:
    image: caddy:2
    container_name: acces-domaine
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./certificats:/data
      - ./config:/config
"""
CADDYFILE = """{
\thttps_port %(port)d
\thttp_port %(http_port)d
\tauto_https disable_redirects
}

%(address)s {
\ttls {
\t\tissuer acme {
\t\t\tdisable_http_challenge
\t\t}
\t}
\theader Strict-Transport-Security "max-age=31536000"
\tredir /.well-known/carddav /remote.php/dav 301
\tredir /.well-known/caldav /remote.php/dav 301
\treverse_proxy 127.0.0.1:%(target)d
}
"""
# Le dossier et les deux fichiers ($1 et $2 : leur contenu, jamais collé dans le texte du script).
DOMAIN_INSTALL = (
    'set -e; mkdir -p "$0"; cd "$0"; printf "%s" "$1" > compose.yaml; printf "%s" "$2" > Caddyfile; '
    "docker compose up -d; docker compose exec -T acces-domaine caddy reload --config /etc/caddy/Caddyfile || true"
)


def caddyfile(address, target_port):
    return CADDYFILE % {"port": DOMAIN_PORT, "http_port": DOMAIN_PORT - 1, "address": address, "target": target_port}


def _domain_access(args):
    target = args.get("port")
    if not isinstance(target, int) or isinstance(target, bool) or not 1024 <= target <= 65535 or target == DOMAIN_PORT:
        raise ActionError(f"Port refusé : {target!r}")
    content = caddyfile(host_name(args.get("adresse")), target)
    return ["sh", "-c", DOMAIN_INSTALL, DOMAIN_FOLDER, DOMAIN_COMPOSE, content], 600


def _remove_domain_access(args):
    return ["sh", "-c", f"cd {DOMAIN_FOLDER} && docker compose down; rm -rf {DOMAIN_FOLDER}"], 120


# ---------------------------------------------------------------- outils en option (host/catalog.py)
# $0 dossier, $1 compose.yaml, $2 .env (modèle), $3 préparation, $4 port https, $5 port local ;
# le mot de passe de l'admin arrive sur l'entrée standard. Un .env déjà là est gardé (avec ses secrets
# et ses comptes : réinstaller un outil retrouve ses données), et le script le dit (« REGLAGES_GARDES ») :
# le mot de passe tiré pour cette fois ne sert alors pas.
OPTIONAL_INSTALL = """set -e
umask 077
mkdir -p "$0"
cd "$0"
printf "%s" "$1" > compose.yaml
pass=$(cat)
if [ -n "$2" ] && [ -f .env ]; then
  echo "REGLAGES_GARDES"  # réinstallation : comptes et secrets de la fois d'avant
fi
if [ -n "$2" ] && [ ! -f .env ]; then
  secret=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
  secret2=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
  name=$(tailscale status --json --peers=false 2>/dev/null | sed -n 's/^ *"DNSName": "\\(.*\\)\\.",*$/\\1/p' | head -1)
  lan=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \\([0-9.]*\\).*/\\1/p' | head -1)
  tsip=$(tailscale ip -4 2>/dev/null | head -1)
  user=$(getent passwd 1000 | cut -d: -f1)
  gid=$(getent passwd 1000 | cut -d: -f4)
  render=$(getent group render | cut -d: -f3)
  printf "%s\n" "$2" | S="$secret" S2="$secret2" P="$pass" T="$name" L="${lan:-127.0.0.1}" I="${tsip:-127.0.0.2}" \\
    U="${user:-root}" G="${gid:-1000}" R="${render:-0}" H="$(hostname)" N="${name#*.}" awk 'BEGIN {
      marks[1] = "@SECRET_TIRE_AU_SORT@"; values[1] = ENVIRON["S"]
      marks[2] = "@MOT_DE_PASSE_ADMIN@"; values[2] = ENVIRON["P"]
      marks[3] = "@ADRESSE_TAILSCALE@"; values[3] = ENVIRON["T"]
      marks[4] = "@IP_LOCALE@"; values[4] = ENVIRON["L"]
      marks[5] = "@IP_TAILSCALE@"; values[5] = ENVIRON["I"]
      marks[6] = "@AUTRE_SECRET@"; values[6] = ENVIRON["S2"]
      marks[7] = "@COMPTE_SERVEUR@"; values[7] = ENVIRON["U"]
      marks[8] = "@UID_SERVEUR@"; values[8] = "1000"
      marks[9] = "@GID_SERVEUR@"; values[9] = ENVIRON["G"]
      marks[10] = "@GID_RENDER@"; values[10] = ENVIRON["R"]
      marks[11] = "@NOM_SERVEUR@"; values[11] = ENVIRON["H"]
      marks[12] = "@SUFFIXE_TAILSCALE@"; values[12] = ENVIRON["N"] }
    { for (n = 1; n <= 12; n++) {
        done = ""; rest = $0
        while ((i = index(rest, marks[n])) > 0) {
          done = done substr(rest, 1, i - 1) values[n]; rest = substr(rest, i + length(marks[n]))
        }
        $0 = done rest
      }
      print }' > .env
fi
[ -z "$3" ] || sh -c "$3"
docker compose up -d
[ -z "$4" ] || tailscale serve --bg --https="$4" "http://127.0.0.1:$5" || echo "Adresse https : active HTTPS Certificates dans la console Tailscale." >&2
"""
OPTIONAL_REMOVE = (
    'cd "$0" && docker compose down; [ -z "$1" ] || tailscale serve --https="$1" off || true; '
    'docker stop "adresse-$(basename "$0")" 2>/dev/null || true; [ -z "$2" ] || sh -c "$2"'
)
# Écrit entre apostrophes dans le .env : tout caractère visible ou espace, sauf l'apostrophe elle-même.
ADMIN_PASSWORD = re.compile(r"[^\x00-\x1f\x7f']{15,128}")


def optional_tool(args):
    from host import catalog

    name = args.get("outil")
    if name not in catalog.OPTIONAL_TOOLS:
        raise ActionError(f"Outil refusé : {name!r}")
    return name, catalog.OPTIONAL_TOOLS[name]


def _install_optional(args):
    name, tool = optional_tool(args)
    files = tool["fichiers"]
    https = str(tool["port_https"] or "")
    local = str(tool["port_local"] or "")
    command = ["sh", "-c", OPTIONAL_INSTALL, f"/opt/stacks/{name}", files["compose.yaml"], files.get(".env", "")]
    return [*command, tool["preparer"], https, local], 1800


def _remove_optional(args):
    name, tool = optional_tool(args)
    cleanup = tool.get("nettoyer", "")
    return ["sh", "-c", OPTIONAL_REMOVE, f"/opt/stacks/{name}", str(tool["port_https"] or ""), cleanup], 300


# ---------------------------------------------------------------- adresse à part d'un outil (services/tool_addresses.py)
# Un conteneur Tailscale par outil, sur le réseau du serveur (les outils n'écoutent que 127.0.0.1) :
# https://<outil>.tailxxxx.ts.net renvoie vers 127.0.0.1:<port>. Mode « userspace », sans toucher au
# Tailscale du serveur ; inscription une seule fois (TS_AUTH_ONCE), l'état est gardé dans ./etat.
# Si un conteneur « adresse-<outil> » existe déjà dans une autre pile, il est gardé (réponse « DEJA »).
# $0 l'outil, $1 son port ; la clé d'inscription (usage unique, 1 heure) arrive sur l'entrée standard.
TOOL_ADDRESS = """set -e
# Déjà une adresse d'une autre installation (ancienne pile « adresses » du guide) : on la garde et on la rallume.
projet=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "adresse-$0" 2>/dev/null || true)
if [ -n "$projet" ] && [ "$projet" != "adresse-$0" ]; then
  docker start "adresse-$0" >/dev/null
  echo DEJA
  exit 0
fi
umask 077
mkdir -p "/opt/stacks/adresse-$0/etat"
cd "/opt/stacks/adresse-$0"
printf 'TS_AUTHKEY=%s\\n' "$(cat)" > .env
printf '{"TCP":{"443":{"HTTPS":true}},"Web":{"${TS_CERT_DOMAIN}:443":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:%s"}}}}}\\n' "$1" > serve.json
cat > compose.yaml <<FIN
services:
  adresse:
    image: tailscale/tailscale:latest
    container_name: adresse-$0
    network_mode: host
    env_file: .env
    environment:
      - TS_HOSTNAME=$0
      - TS_STATE_DIR=/var/lib/tailscale
      - TS_SERVE_CONFIG=/config/serve.json
      - TS_USERSPACE=true
      - TS_AUTH_ONCE=true
      - TS_TAILSCALED_EXTRA_ARGS=--port=0
    volumes:
      - ./etat:/var/lib/tailscale
      - ./serve.json:/config/serve.json:ro
    restart: unless-stopped
FIN
docker compose up -d
"""
# L'outil apprend sa nouvelle adresse (liens, protection des formulaires) : une ligne de son .env, puis relance.
# $0 l'outil, $1 le nom de la variable (fixé dans host/catalog.py), $2 l'adresse (nom vérifié).
ADDRESS_IN_TOOL = (
    'cd "/opt/stacks/$0" && [ -f .env ] && grep -q "^$1=" .env && '
    'sed -i "s#^$1=.*#$1=https://$2#" .env && docker compose up -d'
)
AUTH_KEY = re.compile(r"tskey-auth-[A-Za-z0-9-]{10,200}")


def _address_target(args):
    from services.tool_addresses import TARGETS

    name = args.get("outil")
    if name not in TARGETS:
        raise ActionError(f"Outil refusé : {name!r}")
    return name, TARGETS[name]


def _tool_address(args):
    name, port = _address_target(args)
    return ["sh", "-c", TOOL_ADDRESS, name, str(port)], 300


def _address_in_tool(args):
    from host import catalog

    name, _ = _address_target(args)
    variable = (catalog.OPTIONAL_TOOLS.get(name) or {}).get("variable_adresse")
    if not variable:
        raise ActionError(f"Pas d'adresse à régler dans {name!r}")
    host = host_name(args.get("adresse"))
    if not host.endswith(".ts.net"):
        raise ActionError(f"Adresse refusée : {host!r}")
    return ["sh", "-c", ADDRESS_IN_TOOL, name, variable, host], 300


def optional_password(name, text):
    """Le mot de passe demandé par un outil (Time Machine, Paperless) : 15 à 128 caractères, sans apostrophe."""
    from host import catalog

    if not catalog.OPTIONAL_TOOLS[name]["mot_de_passe"]:
        return ""
    if not ADMIN_PASSWORD.fullmatch(text or ""):
        raise ActionError("Mot de passe refusé : 15 à 128 caractères, sans apostrophe (').")
    return text


# ---------------------------------------------------------------- outils (conteneurs Docker)
CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
STACK_FOLDER = re.compile(r"/opt/(stacks/)?[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
# Chaque geste sur un outil : un script fixe ; le dossier ($1) et le conteneur d'adresse ($2) sont
# passés comme arguments du shell, jamais collés dans le texte.
TOOL_GESTURES = {
    "redemarrer": 'cd "$1" && docker compose restart',
    "demarrer": 'cd "$1" && docker compose up -d && { [ -z "$2" ] || docker start "$2" || true; }',
    "arreter": 'cd "$1" && docker compose stop && { [ -z "$2" ] || docker stop "$2" || true; }',
    "mettre_a_jour": 'cd "$1" && docker compose pull && docker compose up -d && docker image prune -f',
}


def _container(value):
    name = str(value or "")
    if not CONTAINER.fullmatch(name):
        raise ActionError(f"Conteneur refusé : {name!r}")
    return name


def _list_containers(args):
    return ["docker", "ps", "-a", "--format", "{{json .}}"], 30


def _containers_usage(args):
    return ["docker", "stats", "--no-stream", "--format", "{{json .}}"], 30


def _container_image(args):
    return ["docker", "inspect", "--format", "{{.Config.Image}}|{{.Image}}", _container(args.get("nom"))], 10


def _image_details(args):
    image = str(args.get("image") or "")
    if not IMAGE_ID.fullmatch(image):
        raise ActionError(f"Image refusée : {image!r}")
    template = '{{json .RepoDigests}}|{{index .Config.Labels "org.opencontainers.image.version"}}'
    return ["docker", "image", "inspect", "--format", template, image], 10


def _container_running(args):
    return ["docker", "inspect", "-f", "{{.State.Running}}", _container(args.get("nom"))], 10


def _tool(args):
    gesture = args.get("geste")
    if gesture not in TOOL_GESTURES:
        raise ActionError(f"Geste refusé : {gesture!r}")
    folder = str(args.get("dossier") or "")
    if not STACK_FOLDER.fullmatch(folder) or ".." in folder:
        raise ActionError(f"Dossier d'outil refusé : {folder!r}")
    address = _container(args["adresse"]) if args.get("adresse") else ""
    return ["sh", "-c", TOOL_GESTURES[gesture], "outil", folder, address], 1800


# ---------------------------------------------------------------- Nextcloud (occ)
# Les sous-commandes d'administration dont l'appli se sert, et aucune autre.
OCC_COMMANDS = frozenset(
    {
        "status",
        "background:cron",
        "app:list",
        "app:install",
        "app:enable",
        "config:app:get",
        "config:app:set",
        "config:app:delete",
        "files:scan",
        "files_external:list",
        "files_external:create",
        "files_external:applicable",
        "files_external:option",
        "files_external:delete",
        "user:list",
        "user:info",
        "user:add",
        "user:delete",
        "user:enable",
        "user:disable",
        "user:resetpassword",
        "user:setting",
        "group:list",
        "group:info",
        "group:add",
        "group:delete",
        "group:adduser",
        "group:removeuser",
        "groupfolders:list",
        "groupfolders:create",
        "groupfolders:delete",
        "groupfolders:rename",
        "groupfolders:group",
        "groupfolders:quota",
        "config:system:get",
        "config:system:set",
        "config:system:delete",
    }
)
# Réglages système de Nextcloud que l'appli peut lire ou changer (accès par le domaine), et aucun autre.
OCC_SYSTEM_KEYS = frozenset({"trusted_domains", "trusted_proxies", "overwrite.cli.url"})
OCC_BASE = ["-u", "www-data", "nextcloud", "php", "occ", "--no-warnings"]
# Le mot de passe arrive sur l'entrée standard et devient la variable OC_PASS lue par occ :
# il n'apparaît jamais dans la liste des processus du serveur.
WITH_PASSWORD = 'export OC_PASS="$(cat)"; exec "$@"'


def _nextcloud(args):
    command = args.get("commande")
    if command not in OCC_COMMANDS:
        raise ActionError(f"Commande Nextcloud refusée : {command!r}")
    arguments = args.get("arguments") or []
    if not isinstance(arguments, list) or len(arguments) > 40:
        raise ActionError("Arguments Nextcloud refusés")
    for argument in arguments:
        if not isinstance(argument, str) or len(argument) > 8192 or "\x00" in argument:
            raise ActionError(f"Argument Nextcloud refusé : {argument!r}"[:200])
    if command.startswith("config:system:") and (not arguments or arguments[0] not in OCC_SYSTEM_KEYS):
        raise ActionError(f"Réglage Nextcloud refusé : {arguments[:1]!r}")
    timeout = args.get("delai", 60)
    if not isinstance(timeout, int) or not 5 <= timeout <= 3600:
        raise ActionError(f"Délai refusé : {timeout!r}")
    if args.get("mot_de_passe"):
        return [
            "sh",
            "-c",
            WITH_PASSWORD,
            "sh",
            "docker",
            "exec",
            "-e",
            "OC_PASS",
            *OCC_BASE,
            command,
            *arguments,
        ], timeout
    return ["docker", "exec", *OCC_BASE, command, *arguments], timeout


ACTIONS = {
    "installer_antivirus": _install_antivirus,
    "retirer_antivirus": _remove_antivirus,
    "analyser_fichiers": _scan_files,
    "analyser_disque": _scan_disk,
    "liste_conteneurs": _list_containers,
    "conso_conteneurs": _containers_usage,
    "image_du_conteneur": _container_image,
    "details_image": _image_details,
    "conteneur_actif": _container_running,
    "outil": _tool,
    "acces_domaine": _domain_access,
    "installer_outil_option": _install_optional,
    "retirer_outil_option": _remove_optional,
    "adresse_outil": _tool_address,
    "adresse_dans_outil": _address_in_tool,
    "retirer_acces_domaine": _remove_domain_access,
    "nextcloud": _nextcloud,
}
