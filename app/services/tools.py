"""Outils du serveur : les projets Docker (Jellyfin, Nextcloud…) et leurs icônes.

Sommaire :
  NAMES, SYSTEM_TOOLS, PORTS, NEVER_ON_WIFI, FALLBACK_ICONS, OUTSIDE_APP : réglages connus
  list_tools : les outils installés, avec leur état
  find_tool : un outil par son identifiant
  find_icon : l'icône d'un outil (site de l'outil, sinon icône de secours)
  interne : _in_app, _projects, _state, _tool
"""

import json
import re
from urllib.parse import urljoin

import httpx

import config
from host import catalog, commands
from services import frame_check, tool_addresses, tool_credentials
from services.service_error import ServiceError

# ---------------------------------------------------------------- outils (Docker)
NAMES = {
    "jellyfin": ("Jellyfin", "Films et séries"),
    "nextcloud": ("Nextcloud", "Fichiers, photos, agenda"),
    "dockge": ("Dockge", "Gestion avancée des outils"),
    "antivirus": ("Antivirus", "Analyse les fichiers déposés par les invités (ClamAV)"),
    "jabba-app": ("Appli Jabba", "Cette appli"),
    "adresses": ("Adresses", "Les adresses https des outils"),
    **{tool_id: (tool["nom"], tool["role"]) for tool_id, tool in catalog.OPTIONAL_TOOLS.items()},
}
# Outils du système : toujours actifs, sans interrupteur.
SYSTEM_TOOLS = {"jabba-app", "adresses"}
# Conteneur « adresse » (Tailscale) qui suit l'interrupteur de chaque outil (adresse à part :
# services/tool_addresses.py).
ADRESSES = {
    "nextcloud": "adresse-nextcloud",
    "jellyfin": "adresse-jellyfin",
    **{tool_id: f"adresse-{tool_id}" for tool_id in tool_addresses.TARGETS},
}
# Où chaque outil range son icône : l'appli affiche le logo servi par l'outil lui-même.
# Port local de chaque outil : l'appli y va chercher le logo que l'outil fournit lui-même.
PORTS = {
    "jellyfin": 8096,
    "nextcloud": 8081,
    "dockge": 5001,
    **{tool_id: tool["port_local"] for tool_id, tool in catalog.OPTIONAL_TOOLS.items() if tool["port_local"]},
}
# Jamais ouverts aux invités du Wi-Fi : ils donnent la main sur le serveur.
NEVER_ON_WIFI = {"dockge"}
FALLBACK_ICONS = [
    "/web/favicon.png",
    "/web/favicon.ico",
    "/favicon.ico",
    "/favicon.png",
    "/static/img/icons/favicon-32x32.png",
    "/core/img/favicon.ico",
    "/icon.svg",
]
# Ouverts dans un onglet à part (Nextcloud ; les outils en option refusent souvent d'être affichés dans une autre page).
# Jamais dans un cadre : Nextcloud a son propre écran (Fichiers). Ceux de l'Établi : voir frame_check.
OUTSIDE_APP = {"nextcloud"}
# Connus pour s'ouvrir dans un cadre de Jabba, sans avoir à le vérifier.
ALWAYS_IN_APP = {"jellyfin", "dockge"}


def _in_app(pid):
    """Dans un cadre de Jabba : les outils connus pour l'accepter, et ceux de l'Établi qui n'interdisent rien."""
    if pid in OUTSIDE_APP:
        return False
    if pid in ALWAYS_IN_APP:
        return True
    if pid in catalog.OPTIONAL_TOOLS:
        return frame_check.allows_framing(catalog.OPTIONAL_TOOLS[pid]["port_local"])
    return True


def _projects(output):
    """Les conteneurs Docker (une ligne JSON chacun), rangés par pile : {nom: {id, dossier, conteneurs}}."""
    projects = {}
    for line in output.splitlines():
        container = json.loads(line)
        labels = dict(label.split("=", 1) for label in container.get("Labels", "").split(",") if "=" in label)
        project_name = labels.get("com.docker.compose.project", container["Names"])
        folder = labels.get("com.docker.compose.project.working_dir", "")
        project = projects.setdefault(project_name, {"id": project_name, "dossier": folder, "conteneurs": []})
        project["conteneurs"].append(
            {"nom": container["Names"], "etat": container["State"], "statut": container["Status"]}
        )
    return projects


def _state(containers):
    """« actif », « partiel », « arrêté » ou « en panne » (un conteneur qui redémarre en boucle)."""
    if any("restarting" in container["etat"] for container in containers):
        return "en panne"
    states = {container["etat"] for container in containers}
    if states == {"running"}:
        return "actif"
    return "partiel" if "running" in states else "arrêté"


def _tool(pid, project):
    name, role = NAMES.get(pid, (pid, ""))
    return {
        "id": pid,
        "nom": name,
        "role": role,
        "etat": _state(project["conteneurs"]),
        "lien": tool_addresses.address(pid) or config.LINKS.get(pid),
        "dossier": project["dossier"],
        "conteneurs": project["conteneurs"],
        "modifiable": pid != "jabba-app" and bool(project["dossier"]),
        "systeme": pid in SYSTEM_TOOLS,
        "icone": pid in PORTS,
        "dans_appli": _in_app(pid),
        # Outil en option publié sur Tailscale : l'écran en déduit son adresse (même serveur, ce port).
        "port_https": (catalog.OPTIONAL_TOOLS.get(pid) or {}).get("port_https"),
        "en_option": pid in catalog.OPTIONAL_TOOLS,
        # Menu « Identifiants » : le compte créé par Jabba, ou celui que tu y notes (tool_credentials.py).
        "identifiants": pid in tool_credentials.STORABLE,
        "identifiants_jabba": tool_credentials.created_by_jabba(pid),
    }


def list_tools():
    if config.DEMO:
        from services.demo_data import TOOLS

        return [
            {
                **tool,
                "lien": tool_addresses.address(tool["id"]) or tool.get("lien"),
                "identifiants": tool["id"] in tool_credentials.STORABLE,
                "identifiants_jabba": tool_credentials.created_by_jabba(tool["id"]),
            }
            for tool in TOOLS
        ]
    code, output, error_output = commands.run_action("liste_conteneurs")
    if code != 0:
        raise ServiceError(500, "Docker ne répond pas : " + error_output[:200])
    return [
        _tool(pid, project)
        for pid, project in sorted(_projects(output).items())
        if not pid.startswith("adresse-")  # l'adresse à part d'un outil : fait partie de l'outil
    ]


def find_tool(pid):
    for tool in list_tools():
        if tool["id"] == pid:
            if not tool["modifiable"]:
                raise ServiceError(400, "Cet outil ne se gère pas depuis l'appli.")
            if not re.fullmatch(r"/[\w./-]+", tool["dossier"]):
                raise ServiceError(400, "Dossier inattendu.")
            return tool
    raise ServiceError(404, "Outil inconnu")


ICONS_CACHE = {}
RE_LINK = re.compile(r"<link[^>]+>", re.I)


def find_icon(pid):
    base = f"http://127.0.0.1:{PORTS[pid]}/"
    with httpx.Client(timeout=5, follow_redirects=True) as container:
        candidates = []
        try:
            page = container.get(base)
            for tag_name in RE_LINK.findall(page.text):
                relative_path = re.search(r'rel=["\']([^"\']+)', tag_name, re.I)
                href = re.search(r'href=["\']([^"\']+)', tag_name, re.I)
                if relative_path and href and "icon" in relative_path.group(1).lower():
                    priority = 0 if "apple-touch" in relative_path.group(1).lower() else 1
                    candidates.append((priority, urljoin(str(page.url), href.group(1))))
        except httpx.HTTPError:
            pass
        urls = [candidate_url for _, candidate_url in sorted(candidates)] + [
            urljoin(base, project) for project in FALLBACK_ICONS
        ]
        for candidate_url in urls:
            try:
                response = container.get(candidate_url)
            except httpx.HTTPError:
                continue
            media_type = response.headers.get("content-type", "").split(";")[0]
            if (
                response.status_code == 200
                and media_type.startswith("image/")
                and 0 < len(response.content) < 2_000_000
            ):
                return response.content, media_type
    return None
