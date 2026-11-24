"""Après l'installation de Nextcloud ou de Jellyfin depuis l'Établi : ce que le guide faisait à la main.

Jellyfin : l'assistant de démarrage est fait par Jabba (langue, compte admin au nom du compte du serveur,
avec le mot de passe tiré par Jabba, accès à distance sans UPnP), puis les médiathèques Films et Séries.
Nextcloud : attendre la fin de son installation, reconnaître le relais https, lancer ses tâches de fond
par cron, garder la corbeille 30 jours au moins, montrer les films et les téléchargements, et relier
Jabba (écran Fichiers) avec un mot de passe d'application, gardé dans un fichier lisible par l'appli
seule (config.NC_LINK_FILE).

Sommaire :
  after_install(tool, who)  : la configuration de l'outil qu'on vient d'installer ; une ligne de compte rendu
  after_address(tool, url)  : Nextcloud apprend sa nouvelle adresse à part (si Tailscale a dû la changer)
  JELLYFIN, NEXTCLOUD       : leurs adresses sur le serveur
  interne : _wait, _jellyfin, _jellyfin_headers, _nextcloud, _nextcloud_installed, _external_folders, _link_jabba
"""

import json
import time

import httpx

import config
from host import commands
from services import activity_log, json_files, nextcloud_settings, tool_credentials

JELLYFIN = "http://127.0.0.1:8096"
NEXTCLOUD = config.NC_URL
WAIT_SECONDS = 15 * 60
# Les dossiers du serveur montrés dans Nextcloud : nom affiché → dossier dans le conteneur.
EXTERNAL_FOLDERS = {"Films et séries": "/mnt/media", "Téléchargements": "/mnt/downloads"}
CLIENT = 'MediaBrowser Client="Jabba", Device="Jabba", DeviceId="jabba-installation", Version="1"'


def _wait(ready, seconds=WAIT_SECONDS, sleep=time.sleep):
    """Attend que `ready()` réponde vrai (l'outil démarre, ou s'installe) ; faux si le délai passe."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if ready():
                return True
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        sleep(5)
    return False


# ---------------------------------------------------------------- Jellyfin
def _jellyfin_headers(token=None):
    return {"Authorization": CLIENT + (f', Token="{token}"' if token else "")}


def _jellyfin(who):
    login = tool_credentials.read("jellyfin")
    if not login:  # installé à la main avant : son compte est celui que tu avais créé, rien à régler
        return ""
    if not _wait(lambda: httpx.get(JELLYFIN + "/System/Info/Public", timeout=5).status_code == 200):
        return "Jellyfin ne répond pas encore : son assistant sera à faire à la première ouverture."
    info = httpx.get(JELLYFIN + "/System/Info/Public", timeout=5).json()
    headers = _jellyfin_headers()
    if not info.get("StartupWizardCompleted"):
        steps = [
            (
                "/Startup/Configuration",
                {"UICulture": "fr", "MetadataCountryCode": "FR", "PreferredMetadataLanguage": "fr"},
            ),
            ("/Startup/User", {"Name": login["username"], "Password": login["password"]}),
            ("/Startup/RemoteAccess", {"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False}),
        ]
        httpx.get(JELLYFIN + "/Startup/User", headers=headers, timeout=10)  # crée le premier compte, vide
        for path, body in steps:
            httpx.post(JELLYFIN + path, json=body, headers=headers, timeout=20).raise_for_status()
        httpx.post(JELLYFIN + "/Startup/Complete", headers=headers, timeout=20).raise_for_status()
    session = httpx.post(
        JELLYFIN + "/Users/AuthenticateByName",
        json={"Username": login["username"], "Pw": login["password"]},
        headers=headers,
        timeout=20,
    )
    if session.status_code != 200:
        return "Jellyfin était déjà réglé avec un autre compte : rien n'a été changé."
    token = session.json()["AccessToken"]
    existing = {
        folder["Name"]
        for folder in httpx.get(
            JELLYFIN + "/Library/VirtualFolders", headers=_jellyfin_headers(token), timeout=20
        ).json()
    }
    for name, kind, path in (("Films", "movies", "/data/media/films"), ("Séries", "tvshows", "/data/media/series")):
        if name in existing:
            continue
        httpx.post(
            JELLYFIN + "/Library/VirtualFolders",
            params={"name": name, "collectionType": kind, "paths": path, "refreshLibrary": "true"},
            json={"LibraryOptions": {"PreferredMetadataLanguage": "fr", "MetadataCountryCode": "FR"}},
            headers=_jellyfin_headers(token),
            timeout=60,
        ).raise_for_status()
    activity_log.record("outil", "Jellyfin réglé : compte, langue, médiathèques Films et Séries", who)
    return "Jellyfin réglé : compte (menu … → Identifiants), médiathèques Films et Séries."


# ---------------------------------------------------------------- Nextcloud
def _nextcloud_installed():
    code, output, _ = commands.occ(["status", "--output=json"])
    return code == 0 and json.loads(output[output.index("{") :]).get("installed") is True


def _external_folders():
    """Films et téléchargements visibles dans Nextcloud (une seule fois chacun)."""
    commands.occ(["app:enable", "files_external"])
    code, output, _ = commands.occ(["files_external:list", "--output=json"])
    mounted = {mount.get("mount_point", "").strip("/") for mount in json.loads(output or "[]")} if code == 0 else set()
    for name, folder in EXTERNAL_FOLDERS.items():
        if name not in mounted:
            commands.occ(["files_external:create", name, "local", "null::null", "-c", f"datadir={folder}"])


def _link_jabba(login):
    """Un mot de passe d'application pour l'écran Fichiers de Jabba, demandé à Nextcloud avec le compte admin."""
    response = httpx.get(
        NEXTCLOUD + "/ocs/v2.php/core/getapppassword",
        params={"format": "json"},
        headers={"OCS-APIRequest": "true"},
        auth=(login["username"], login["password"]),
        timeout=30,
    )
    response.raise_for_status()
    app_password = response.json()["ocs"]["data"]["apppassword"]
    json_files.save(config.NC_LINK_FILE, {"user": login["username"], "app_password": app_password}, private=True)
    config.NC_USER = login["username"]  # pris en compte tout de suite, sans redémarrer l'appli
    config.NC_APP_PASSWORD = app_password


def _nextcloud(who):
    if not _wait(_nextcloud_installed):
        return "Nextcloud s'installe encore : rouvre l'Établi dans quelques minutes."
    nextcloud_settings.add_to_system_list("trusted_proxies", "172.16.0.0/12")  # le https des adresses Tailscale
    commands.occ(["background:cron"])
    nextcloud_settings.keep_deleted_files()  # un fichier supprimé depuis un ordinateur se récupère 30 jours
    _external_folders()
    login = tool_credentials.read("nextcloud")
    if login and not config.NC_APP_PASSWORD:
        _link_jabba(login)
    activity_log.record("outil", "Nextcloud réglé : tâches de fond, films et téléchargements, lien avec Jabba", who)
    return "Nextcloud réglé : écran Fichiers relié, films et téléchargements visibles."


def after_install(tool, who=None):
    if config.DEMO or tool not in ("jellyfin", "nextcloud"):
        return ""
    try:
        return _jellyfin(who) if tool == "jellyfin" else _nextcloud(who)
    except (httpx.HTTPError, ValueError, KeyError, OSError) as error:
        return f"Réglage automatique de {tool} interrompu ({type(error).__name__}) : à finir à la première ouverture."


def after_address(tool, url):
    if tool == "nextcloud" and not config.DEMO:
        nextcloud_settings.add_to_system_list("trusted_domains", url.removeprefix("https://"))
