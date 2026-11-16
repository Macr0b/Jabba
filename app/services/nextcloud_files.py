"""services.nextcloud_files"""

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote, unquote
import httpx
from fastapi import HTTPException
import config


# ---------------------------------------------------------------- fichiers (Nextcloud)
DAV_NS = {"d": "DAV:", "oc": "http://owncloud.org/ns", "nc": "http://nextcloud.org/ns"}
PROPFIND = """<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
 <d:prop><d:getlastmodified/><d:getcontentlength/><d:resourcetype/><oc:size/><oc:permissions/><d:getcontenttype/><nc:mount-type/></d:prop>
</d:propfind>"""


def require_nextcloud():
    if config.DEMO:
        return
    if not (config.NC_USER and config.NC_APP_PASSWORD):
        raise HTTPException(503, "Le lien avec Nextcloud n'est pas encore réglé (fichier .env).")


def clean_path(path: str) -> str:
    shares = [parts_list for parts_list in (path or "").split("/") if parts_list not in ("", ".")]
    if any(parts_list == ".." for parts_list in shares):
        raise HTTPException(400, "Chemin invalide")
    return "/" + "/".join(shares)


def dav_url(path: str) -> str:
    base = f"/remote.php/dav/files/{quote(config.NC_USER)}"
    return config.NC_URL + base + quote(clean_path(path))


def client():
    headers = {"Host": config.NC_HOST} if config.NC_HOST else {}
    return httpx.AsyncClient(
        auth=(config.NC_USER, config.NC_APP_PASSWORD), headers=headers, timeout=httpx.Timeout(60, read=600)
    )


def dav_entry(rep):
    """Une réponse WebDAV de Nextcloud -> un élément de l'écran Fichiers."""
    prefix = f"/remote.php/dav/files/{config.NC_USER}"
    href = unquote(rep.find("d:href", DAV_NS).text)
    relative_path = "/" + href.split(prefix, 1)[-1].strip("/")
    prop = rep.find("d:propstat/d:prop", DAV_NS)
    folder_flag = prop.find("d:resourcetype/d:collection", DAV_NS) is not None
    permissions = prop.findtext("oc:permissions", "", DAV_NS) or ""
    size = prop.findtext("oc:size", None, DAV_NS) or prop.findtext("d:getcontentlength", None, DAV_NS)
    modified = prop.findtext("d:getlastmodified", None, DAV_NS)
    return {
        "nom": relative_path.rsplit("/", 1)[-1] or "Nextcloud",
        "chemin": relative_path,
        "dossier": folder_flag,
        "taille": int(size) if size and size.lstrip("-").isdigit() and int(size) >= 0 else None,
        "modifie": parsedate_to_datetime(modified).isoformat() if modified else None,
        "type": prop.findtext("d:getcontenttype", None, DAV_NS),
        "ecriture": any(letter in permissions for letter in ("W", "C", "K")),
        "externe": prop.findtext("nc:mount-type", "", DAV_NS) == "external",
    }


async def read_folder(path):
    if config.DEMO:
        from services.demo_data import folder_listing

        return folder_listing(path)
    async with client() as c:
        response = await c.request(
            "PROPFIND", dav_url(path), content=PROPFIND, headers={"Depth": "1", "Content-Type": "application/xml"}
        )
    if response.status_code == 404:
        raise HTTPException(404, "Dossier introuvable")
    if response.status_code == 401:
        raise HTTPException(502, "Nextcloud refuse le mot de passe d'application (fichier .env).")
    if response.status_code >= 400:
        raise HTTPException(502, f"Nextcloud a répondu {response.status_code}")
    root = ET.fromstring(response.content)
    entries, folder = [], None
    for rep in root.findall("d:response", DAV_NS):
        item = dav_entry(rep)
        relative_path = item["chemin"]
        if relative_path.rstrip("/") == path.rstrip("/") or (relative_path == "/" and path == "/"):
            folder = item
        else:
            entries.append(item)
    entries.sort(key=lambda entry: (not entry["dossier"], entry["nom"].lower()))
    return {
        "chemin": path,
        "elements": entries,
        "ecriture": folder["ecriture"] if folder else True,
        "lien_nextcloud": f"{config.NC_PUBLIC}/apps/files/?dir={quote(path)}" if config.NC_PUBLIC else None,
    }


async def is_nextcloud_folder(path):
    async with client() as c:
        response = await c.request(
            "PROPFIND", dav_url(path), content=PROPFIND, headers={"Depth": "0", "Content-Type": "application/xml"}
        )
    if response.status_code >= 400:
        return False
    root = ET.fromstring(response.content)
    return root.find(".//d:resourcetype/d:collection", DAV_NS) is not None


async def check_response(response, what):
    if response.status_code in (403, 405):
        raise HTTPException(403, f"{what} impossible ici (dossier en lecture seule ?).")
    if response.status_code == 409:
        raise HTTPException(409, f"{what} impossible : le dossier parent n'existe pas.")
    if response.status_code == 412:
        raise HTTPException(409, f"{what} impossible : un élément porte déjà ce nom.")
    if response.status_code >= 400:
        raise HTTPException(502, f"{what} : Nextcloud a répondu {response.status_code}")


# ---------------------------------------------------------------- recherche
SEARCH = """<?xml version="1.0" encoding="UTF-8"?>
<d:searchrequest xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
 <d:basicsearch>
  <d:select><d:prop><d:getlastmodified/><d:getcontentlength/><d:resourcetype/><oc:size/><oc:permissions/><d:getcontenttype/><nc:mount-type/></d:prop></d:select>
  <d:from><d:scope><d:href>/files/{user}</d:href><d:depth>infinity</d:depth></d:scope></d:from>
  <d:where><d:like><d:prop><d:displayname/></d:prop><d:literal>%{texte}%</d:literal></d:like></d:where>
  <d:orderby/>
  <d:limit><d:nresults>60</d:nresults></d:limit>
 </d:basicsearch>
</d:searchrequest>"""


# ---------------------------------------------------------------- corbeille
TRASH_PROPFIND = """<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
 <d:prop><nc:trashbin-filename/><nc:trashbin-original-location/><nc:trashbin-deletion-time/><d:resourcetype/><oc:size/><d:getcontentlength/></d:prop>
</d:propfind>"""


def trash_url(identifier=""):
    base = f"/remote.php/dav/trashbin/{quote(config.NC_USER)}/trash"
    return config.NC_URL + base + ("/" + quote(identifier, safe="") if identifier else "")


async def nextcloud_trash():
    async with client() as c:
        response = await c.request(
            "PROPFIND", trash_url(), content=TRASH_PROPFIND, headers={"Depth": "1", "Content-Type": "application/xml"}
        )
    if response.status_code >= 400:
        return []
    result = []
    for rep in ET.fromstring(response.content).findall("d:response", DAV_NS):
        href = unquote(rep.find("d:href", DAV_NS).text).rstrip("/")
        identifier = href.rsplit("/", 1)[-1]
        if identifier == "trash":
            continue
        prop = rep.find("d:propstat/d:prop", DAV_NS)
        size = prop.findtext("oc:size", None, DAV_NS) or prop.findtext("d:getcontentlength", None, DAV_NS)
        when = prop.findtext("nc:trashbin-deletion-time", "0", DAV_NS)
        result.append(
            {
                "id": identifier,
                "nom": prop.findtext("nc:trashbin-filename", identifier, DAV_NS),
                "origine": "/" + (prop.findtext("nc:trashbin-original-location", "", DAV_NS) or "").strip("/"),
                "supprime": int(when) if when.isdigit() else 0,
                "dossier": prop.find("d:resourcetype/d:collection", DAV_NS) is not None,
                "taille": int(size) if size and size.isdigit() else None,
                "source": "nextcloud",
            }
        )
    return result
