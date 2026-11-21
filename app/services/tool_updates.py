"""Mises à jour des outils (Nextcloud, Jellyfin…) : y a-t-il une version plus récente de leur image ?

Sommaire
- parse_reference(image)                 : (registre, dépôt, étiquette) d'une image Docker.
- remote_digest(registry, repo, tag, get): l'empreinte publiée aujourd'hui pour cette étiquette.
- check_all()                            : compare chaque outil, garde le résultat, alerte une fois.
- summary()                              : le dernier résultat, outil par outil.
- watch_tool_updates()                   : appelé par la surveillance ; une fois par jour.

Principe : une image téléchargée garde l'empreinte (sha256) de ce que le registre
publiait ce jour-là. Si le registre publie aujourd'hui une autre empreinte pour la
même étiquette (« latest », « 32 »…), une version plus récente existe. On ne télécharge
rien : le bouton « Mettre à jour » de l'Établi reste un clic de l'admin.
  forget  : après « Mettre à jour » : l'outil est à jour (on revérifiera au prochain passage)
  interne : _read_state, _write_state, _anonymous_token, _http_get, _local_images
"""

import json
import os
import re
import time

import httpx

import config
from host import actions, commands
from services import json_files, notifications, tools

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
STATE_FILE = os.path.join(DATA_DIR, "mises-a-jour-outils.json")
CHECK_EVERY_SECONDS = 24 * 3600
MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
    ]
)


def _read_state():
    return json_files.load(STATE_FILE, {"outils": {}, "lu": 0})


def _write_state(state):
    json_files.save(STATE_FILE, state)


# ---------------------------------------------------------------- registre
def parse_reference(image):
    """« nextcloud:32 » → (registry-1.docker.io, library/nextcloud, 32) ; « ghcr.io/a/b » → (ghcr.io, a/b, latest)."""
    image = image.split("@", 1)[0]
    name, tag = image, "latest"
    if ":" in image.rsplit("/", 1)[-1]:
        name, tag = image.rsplit(":", 1)
    first = name.split("/", 1)[0]
    if "/" in name and ("." in first or ":" in first or first == "localhost"):
        registry, repository = name.split("/", 1)
    else:
        registry, repository = "registry-1.docker.io", name
        if "/" not in repository:
            repository = "library/" + repository
    if registry == "docker.io":
        registry = "registry-1.docker.io"
    return registry, repository, tag


def _anonymous_token(challenge, get):
    """Jeton anonyme demandé au service indiqué par la réponse 401 (Docker Hub, ghcr.io, lscr.io…)."""
    fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    realm = fields.pop("realm", "")
    if not realm.startswith("https://"):
        return None
    response = get(realm, params=fields, headers={})
    if response.status_code != 200:
        return None
    body = response.json()
    return body.get("token") or body.get("access_token")


def remote_digest(registry, repository, tag, get):
    url = f"https://{registry}/v2/{repository}/manifests/{tag}"
    headers = {"Accept": MANIFEST_TYPES}
    response = get(url, headers=headers, method="HEAD")
    if response.status_code == 401:
        token = _anonymous_token(response.headers.get("www-authenticate", ""), get)
        if not token:
            return None
        headers["Authorization"] = "Bearer " + token
        response = get(url, headers=headers, method="HEAD")
    if response.status_code != 200:
        return None
    return response.headers.get("docker-content-digest")


def _http_get(url, params=None, headers=None, method="GET"):
    return httpx.request(method, url, params=params, headers=headers, timeout=20, follow_redirects=True)


# ---------------------------------------------------------------- ce qui tourne
def _local_images(container_names):
    """Pour chaque conteneur : son image, ses empreintes connues et sa version (étiquette OCI)."""
    found = {}
    for name in container_names:
        code, output, _ = commands.run_action("image_du_conteneur", nom=name)
        if code != 0 or "|" not in output:
            continue
        image, image_id = output.strip().split("|", 1)
        try:
            code, output, _ = commands.run_action("details_image", image=image_id)
        except actions.ActionError:
            continue
        if code != 0 or "|" not in output:
            continue
        digests_json, version = output.strip().split("|", 1)
        try:
            digests = [entry.split("@", 1)[1] for entry in json.loads(digests_json) if "@" in entry]
        except ValueError:
            digests = []
        found[name] = {"image": image, "empreintes": digests, "version": version if version != "<no value>" else ""}
    return found


def check_all(now=None, get=_http_get):
    now = time.time() if now is None else now
    previous = _read_state().get("outils", {})
    current = {}
    for tool in tools.list_tools():
        if tool["id"] in tools.SYSTEM_TOOLS or not tool.get("modifiable"):
            continue
        images = _local_images([container["nom"] for container in tool["conteneurs"]])
        newer = []
        versions = []
        for image in images.values():
            registry, repository, tag = parse_reference(image["image"])
            try:
                digest = remote_digest(registry, repository, tag, get)
            except httpx.HTTPError:
                digest = None
            if digest and image["empreintes"] and digest not in image["empreintes"]:
                newer.append(image["image"])
            if image["version"]:
                versions.append(image["version"])
        entry = {
            "nom": tool["nom"],
            "version": versions[0] if versions else "",
            "a_jour": not newer,
            "images": newer,
            "lu": int(now),
        }
        current[tool["id"]] = entry
        already_known = previous.get(tool["id"], {}).get("images") == newer
        if newer and not already_known:
            notifications.send(
                "mises_a_jour",
                f"Mise à jour disponible : {tool['nom']}",
                "Installe-la depuis l'Établi (bouton « Mettre à jour »). Tes réglages et tes fichiers sont conservés.",
                "/#/outils",
                family="outil",
            )
    state = {"outils": current, "lu": int(now)}
    _write_state(state)
    return state


def summary():
    return _read_state()


def watch_tool_updates():
    if config.DEMO or time.time() - (_read_state().get("lu") or 0) < CHECK_EVERY_SECONDS:
        return
    check_all()


def forget(pid):
    """Après « Mettre à jour » : l'outil est à jour (on revérifiera au prochain passage)."""
    state = _read_state()
    if pid in state.get("outils", {}):
        state["outils"][pid].update(a_jour=True, images=[])
        _write_state(state)
