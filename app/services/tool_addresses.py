"""Une adresse à part pour chaque outil (paperless.tailxxxx.ts.net, homebox.tailxxxx.ts.net…).

Tous les outils partageaient l'adresse du serveur, avec un numéro de port différent : le trousseau
du navigateur ignore le port, mélangeait les comptes et écrasait un mot de passe par un autre.
Chaque outil reçoit donc son propre nom dans Tailscale : un petit conteneur « adresse-<outil> »
(Tailscale, sur le réseau du serveur) qui renvoie https://<outil>.tailxxxx.ts.net vers le port de
l'outil sur le serveur. L'ancienne adresse (avec le port) continue de marcher.

Pour inscrire ces noms, Jabba a besoin d'une clé d'accès à l'API Tailscale, donnée une fois au script
d'installation (ou collée dans la carte « Adresses des outils »). Avec elle, il crée pour chaque outil une
clé d'inscription à usage unique (1 heure), lance le conteneur, et désactive l'expiration de la nouvelle
machine (sinon l'adresse tomberait au bout de 6 mois). L'adresse se crée pendant l'activation de l'outil.
La clé est gardée jusqu'à son expiration (90 jours au plus), dans un fichier lisible par l'appli seule ;
Tailscale la refuse ensuite, Jabba l'oublie et la carte en redemande une s'il manque une adresse.

Sommaire :
  TARGETS                  : les outils qui ont une page, et leur port sur le serveur
  status(installed)        : clé présente ?, l'adresse de chaque outil installé (ou ce qui se passe)
  save_token(token)        : vérifie la clé d'accès auprès de Tailscale, puis la garde (privée)
  forget_token()           : l'oublie
  create_missing(installed, who) : crée, dans un fil à part, les adresses qui manquent (carte de réparation)
  after_install(tool, who) : crée l'adresse d'un outil qu'on vient d'activer (si la clé est là)
  watch()                  : au démarrage puis chaque heure, crée les adresses qui manquent (serveur déménagé…)
  address(tool)            : l'adresse https de l'outil, ou None
  interne : _api, _new_auth_key, _wait_for_device, _create, _timestamp
  AddressError  : ce que Tailscale ou le serveur a refusé, en clair
"""

import calendar
import os
import re
import threading
import time

import httpx

import config
from host import catalog, commands
from services import activity_log, json_files

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
TOKEN_FILE = os.path.join(DATA_DIR, "tailscale-api.json")
ADDRESSES_FILE = os.path.join(DATA_DIR, "adresses-outils.json")
API = "https://api.tailscale.com/api/v2"
KEYS_PAGE = "https://login.tailscale.com/admin/settings/keys"
TOKEN = re.compile(r"tskey-api-[A-Za-z0-9-]{10,200}")
DEVICE_NAME = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
# Les outils qui ont une page où l'on se connecte, et le port où ils répondent sur le serveur.
# Nextcloud et Jellyfin aussi (installés à la main avant la 0.16.6, ils gardent l'adresse qu'ils avaient :
# réponse « DEJA ») ; changedetection n'a pas de compte : rien que le trousseau puisse confondre.
WITHOUT_LOGIN = {"changedetection"}
TARGETS = {
    "dockge": 5001,
    **{
        tool_id: tool["port_local"]
        for tool_id, tool in catalog.OPTIONAL_TOOLS.items()
        if tool["port_local"] and tool_id not in WITHOUT_LOGIN
    },
}
WAIT_SECONDS = 120
_STATE = {}  # outil -> "en cours" | message d'erreur
_LOCK = threading.Lock()


class AddressError(Exception):
    """Ce que Tailscale ou le serveur a refusé, en clair."""


def _token():
    return json_files.load(TOKEN_FILE, {}).get("key", "")


def _api(method, path, payload=None, token=None):
    token = token or _token()
    if not token:
        raise AddressError("Pas de clé Tailscale : colle-la dans la carte « Adresses des outils ».")
    try:
        response = httpx.request(
            method, API + path, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=20
        )
    except httpx.HTTPError:
        raise AddressError("Tailscale ne répond pas : réessaie dans un moment.")
    if response.status_code in (401, 403):
        if token == _token():
            forget_token()  # expirée ou révoquée : inutile de la garder
        raise AddressError("Tailscale refuse la clé (expirée ou révoquée) : crées-en une nouvelle.")
    if response.status_code >= 400:
        raise AddressError(f"Tailscale a refusé la demande ({response.status_code}).")
    return response.json() if response.content else None


def address(tool):
    value = json_files.load(ADDRESSES_FILE, {}).get(tool)
    return value if isinstance(value, str) else None


def status(installed):
    """Pour la carte : la clé est-elle là ? et, pour chaque outil installé qui a une page, son adresse."""
    entries = []
    for tool in sorted(set(installed) & set(TARGETS)):
        name = catalog.OPTIONAL_TOOLS.get(tool, {}).get("name", "Dockge" if tool == "dockge" else tool)
        entries.append({"id": tool, "name": name, "address": address(tool), "state": _STATE.get(tool)})
    return {"key_ready": bool(_token()), "keys_page": KEYS_PAGE, "tools": entries}


def save_token(token):
    token = token.strip() if isinstance(token, str) else ""
    if not TOKEN.fullmatch(token):
        raise AddressError("Ce n'est pas une clé d'accès Tailscale : elle commence par « tskey-api- ».")
    if not config.DEMO:
        _api("GET", "/tailnet/-/devices", token=token)  # la clé doit marcher avant d'être gardée
    json_files.save(TOKEN_FILE, {"key": token}, private=True)


def forget_token():
    json_files.save(TOKEN_FILE, {}, private=True)


def _new_auth_key(tool):
    """Une clé d'inscription pour une seule machine, valable une heure : inutile une fois servie."""
    created = _api(
        "POST",
        "/tailnet/-/keys",
        {
            "capabilities": {"devices": {"create": {"reusable": False, "ephemeral": False, "preauthorized": True}}},
            "expirySeconds": 3600,
            "description": f"Jabba adresse {tool}",
        },
    )
    return created["key"]


def _wait_for_device(tool, since, now=time.time, sleep=time.sleep):
    """La machine « <outil> » inscrite à l'instant (Tailscale ajoute -1, -2… si le nom est déjà pris)."""
    deadline = now() + WAIT_SECONDS
    while now() < deadline:
        devices = (_api("GET", "/tailnet/-/devices") or {}).get("devices", [])
        fresh = [
            device
            for device in devices
            if device.get("hostname") == tool
            and DEVICE_NAME.fullmatch(device.get("name", ""))
            and _timestamp(device.get("created")) >= since - 60
        ]
        if fresh:
            return max(fresh, key=lambda device: _timestamp(device.get("created")))
        sleep(5)
    raise AddressError("La nouvelle adresse n'est pas apparue dans Tailscale : réessaie dans un moment.")


def _timestamp(text):
    try:
        return float(calendar.timegm(time.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return 0.0


def _create(tool, who):
    started = time.time()
    if config.DEMO:
        time.sleep(1)
        url = f"https://{tool}.exemple.ts.net"
    else:
        key = _new_auth_key(tool)
        code, output, error_output = commands.run_action("adresse_outil", input_text=key, outil=tool)
        if code != 0:
            raise AddressError(error_output.strip()[-300:] or "Le conteneur d'adresse n'a pas démarré.")
        # « DEJA » : l'outil avait déjà son adresse (ancienne installation) ; on reprend sa machine.
        device = _wait_for_device(tool, 0 if "DEJA" in output else started)
        _api("POST", f"/device/{device['id']}/key", {"keyExpiryDisabled": True})
        url = "https://" + device["name"].rstrip(".")
        from services import tool_setup  # Nextcloud doit accepter sa nouvelle adresse

        tool_setup.after_address(tool, url)
        if catalog.OPTIONAL_TOOLS.get(tool, {}).get("address_variable"):
            commands.run_action("adresse_dans_outil", outil=tool, address=url.removeprefix("https://"))
    with _LOCK:
        saved = json_files.load(ADDRESSES_FILE, {})
        saved[tool] = url
        json_files.save(ADDRESSES_FILE, saved)
    activity_log.record("outil", f"Adresse de {tool} : {url}", who)


def create_missing(installed, who=None):
    """Lance la création des adresses qui manquent. Renvoie la liste des outils pris en charge."""
    todo = [
        tool for tool in sorted(set(installed) & set(TARGETS)) if not address(tool) and _STATE.get(tool) != "en cours"
    ]
    if not todo:
        return []
    if not _token() and not config.DEMO:
        raise AddressError("Colle d'abord une clé d'accès Tailscale.")
    for tool in todo:
        _STATE[tool] = "en cours"

    def work():
        for tool in todo:
            try:
                _create(tool, who)
                _STATE.pop(tool, None)
            except (AddressError, KeyError, TypeError) as error:
                _STATE[tool] = str(error) if isinstance(error, AddressError) else "Réponse inattendue de Tailscale."

    threading.Thread(target=work, daemon=True).start()
    return todo


def watch():
    """Au démarrage puis chaque heure : si la clé est là, crée les adresses qui manquent (après un déménagement,
    le nouveau serveur refait celles des outils repris ; une adresse ratée est réessayée l'heure suivante)."""
    if not _token():
        return
    from services import tools

    try:
        create_missing({tool["id"] for tool in tools.list_tools()}, "Jabba")
    except AddressError:
        pass


def after_install(tool, who=None):
    """Juste après l'activation d'un outil : son adresse à part. Renvoie une ligne pour le compte rendu."""
    if tool not in TARGETS or address(tool) or not _token():
        return ""
    try:
        _create(tool, who)
    except (AddressError, KeyError, TypeError) as error:
        _STATE[tool] = str(error) if isinstance(error, AddressError) else "Réponse inattendue de Tailscale."
        return "Adresse à part non créée : " + _STATE[tool]
    _STATE.pop(tool, None)
    return "Adresse : " + address(tool)
