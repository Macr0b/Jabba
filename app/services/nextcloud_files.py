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


def clean_path(chemin: str) -> str:
    morceaux = [m for m in (chemin or "").split("/") if m not in ("", ".")]
    if any(m == ".." for m in morceaux):
        raise HTTPException(400, "Chemin invalide")
    return "/" + "/".join(morceaux)


def dav_url(chemin: str) -> str:
    base = f"/remote.php/dav/files/{quote(config.NC_USER)}"
    return config.NC_URL + base + quote(clean_path(chemin))


def client():
    entetes = {"Host": config.NC_HOST} if config.NC_HOST else {}
    return httpx.AsyncClient(auth=(config.NC_USER, config.NC_APP_PASSWORD), headers=entetes, timeout=httpx.Timeout(60, read=600))


def dav_entry(rep):
    """Une réponse WebDAV de Nextcloud -> un élément de l'écran Fichiers."""
    prefixe = f"/remote.php/dav/files/{config.NC_USER}"
    href = unquote(rep.find("d:href", DAV_NS).text)
    rel = "/" + href.split(prefixe, 1)[-1].strip("/")
    prop = rep.find("d:propstat/d:prop", DAV_NS)
    est_dossier = prop.find("d:resourcetype/d:collection", DAV_NS) is not None
    perms = (prop.findtext("oc:permissions", "", DAV_NS) or "")
    taille = prop.findtext("oc:size", None, DAV_NS) or prop.findtext("d:getcontentlength", None, DAV_NS)
    modif = prop.findtext("d:getlastmodified", None, DAV_NS)
    return {
        "nom": rel.rsplit("/", 1)[-1] or "Nextcloud", "chemin": rel, "dossier": est_dossier,
        "taille": int(taille) if taille and taille.lstrip("-").isdigit() and int(taille) >= 0 else None,
        "modifie": parsedate_to_datetime(modif).isoformat() if modif else None,
        "type": prop.findtext("d:getcontenttype", None, DAV_NS),
        "ecriture": any(x in perms for x in ("W", "C", "K")),
        "externe": prop.findtext("nc:mount-type", "", DAV_NS) == "external",
    }


async def read_folder(chemin):
    if config.DEMO:
        from services.demo_data import folder_listing
        return folder_listing(chemin)
    async with client() as c:
        r = await c.request("PROPFIND", dav_url(chemin), content=PROPFIND, headers={"Depth": "1", "Content-Type": "application/xml"})
    if r.status_code == 404:
        raise HTTPException(404, "Dossier introuvable")
    if r.status_code == 401:
        raise HTTPException(502, "Nextcloud refuse le mot de passe d'application (fichier .env).")
    if r.status_code >= 400:
        raise HTTPException(502, f"Nextcloud a répondu {r.status_code}")
    racine = ET.fromstring(r.content)
    elements, dossier = [], None
    for rep in racine.findall("d:response", DAV_NS):
        item = dav_entry(rep)
        rel = item["chemin"]
        if rel.rstrip("/") == chemin.rstrip("/") or (rel == "/" and chemin == "/"):
            dossier = item
        else:
            elements.append(item)
    elements.sort(key=lambda e: (not e["dossier"], e["nom"].lower()))
    return {
        "chemin": chemin, "elements": elements,
        "ecriture": dossier["ecriture"] if dossier else True,
        "lien_nextcloud": f"{config.NC_PUBLIC}/apps/files/?dir={quote(chemin)}" if config.NC_PUBLIC else None,
    }


async def is_nextcloud_folder(chemin):
    async with client() as c:
        r = await c.request("PROPFIND", dav_url(chemin), content=PROPFIND, headers={"Depth": "0", "Content-Type": "application/xml"})
    if r.status_code >= 400:
        return False
    racine = ET.fromstring(r.content)
    return racine.find(".//d:resourcetype/d:collection", DAV_NS) is not None


async def check_response(r, quoi):
    if r.status_code in (403, 405) :
        raise HTTPException(403, f"{quoi} impossible ici (dossier en lecture seule ?).")
    if r.status_code == 409:
        raise HTTPException(409, f"{quoi} impossible : le dossier parent n'existe pas.")
    if r.status_code == 412:
        raise HTTPException(409, f"{quoi} impossible : un élément porte déjà ce nom.")
    if r.status_code >= 400:
        raise HTTPException(502, f"{quoi} : Nextcloud a répondu {r.status_code}")


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


def trash_url(ident=""):
    base = f"/remote.php/dav/trashbin/{quote(config.NC_USER)}/trash"
    return config.NC_URL + base + ("/" + quote(ident, safe="") if ident else "")


async def nextcloud_trash():
    async with client() as c:
        r = await c.request("PROPFIND", trash_url(), content=TRASH_PROPFIND, headers={"Depth": "1", "Content-Type": "application/xml"})
    if r.status_code >= 400:
        return []
    res = []
    for rep in ET.fromstring(r.content).findall("d:response", DAV_NS):
        href = unquote(rep.find("d:href", DAV_NS).text).rstrip("/")
        ident = href.rsplit("/", 1)[-1]
        if ident == "trash":
            continue
        prop = rep.find("d:propstat/d:prop", DAV_NS)
        taille = prop.findtext("oc:size", None, DAV_NS) or prop.findtext("d:getcontentlength", None, DAV_NS)
        quand_ = prop.findtext("nc:trashbin-deletion-time", "0", DAV_NS)
        res.append({
            "id": ident, "nom": prop.findtext("nc:trashbin-filename", ident, DAV_NS),
            "origine": "/" + (prop.findtext("nc:trashbin-original-location", "", DAV_NS) or "").strip("/"),
            "supprime": int(quand_) if quand_.isdigit() else 0,
            "dossier": prop.find("d:resourcetype/d:collection", DAV_NS) is not None,
            "taille": int(taille) if taille and taille.isdigit() else None, "source": "nextcloud",
        })
    return res
