"""routes.player"""
import asyncio
import base64
import json
import os
import re
import subprocess
import time
import httpx
from services import video_player
from services import disk_browser
from services import activity_log
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response
import config
from security import guard
from services import files_source
from services import nextcloud_files
from services import tools
from fastapi import APIRouter

router = APIRouter()

# ---------------------------------------------------------------- lecteur vidéo (conversion à la volée)
def playback_source(chemin):
    """Où ffmpeg lit le fichier : sur le disque si possible, sinon par Nextcloud."""
    chemin = nextcloud_files.clean_path(chemin)
    if files_source.files_source() == "disque":
        return {"entree": files_source.run_disk_action(lambda: disk_browser.read_path(chemin, *files_source.disk_params()))}
    premier, _, reste = chemin.strip("/").partition("/")
    connus = files_source.known_disks()
    if premier in connus:   # un disque externe : lu directement, sans passer par Nextcloud
        base = os.path.realpath(connus[premier])
        reel = os.path.realpath(os.path.join(base, reste))
        if (reel == base or reel.startswith(base + os.sep)) and os.path.isfile(reel):
            return {"entree": reel}
    nextcloud_files.require_nextcloud()
    jeton = base64.b64encode(f"{config.NC_USER}:{config.NC_APP_PASSWORD}".encode()).decode()
    return {"entree": nextcloud_files.dav_url(chemin), "entetes": f"Authorization: Basic {jeton}\r\n"}


_MEDIA_INFO_CACHE = {}   # chemin -> (moment, infos) : l'analyse n'est refaite qu'une fois par film (10 min)


def media_info(chemin):
    deja = _MEDIA_INFO_CACHE.get(chemin)
    if deja and time.time() - deja[0] < 600:
        return dict(deja[1])
    try:
        infos = video_player.probe(playback_source(chemin))
    except RuntimeError as e:
        raise HTTPException(415, str(e))
    except FileNotFoundError:
        raise HTTPException(500, "Le moteur vidéo (ffmpeg) manque dans l'appli.")
    _MEDIA_INFO_CACHE[chemin] = (time.time(), infos)
    return dict(infos)


# ---------------------------------------------------------------- Jellyfin : ouvrir un film depuis Fichiers
JELLYFIN_URL = {"t": 0, "index": {}}
JELLYFIN_KEY_FILE = os.path.join(os.environ.get("JABBA_DATA", "/data"), "jellyfin.json")


def jellyfin_key():
    try:
        with open(JELLYFIN_KEY_FILE) as f:
            return json.load(f).get("cle") or ""
    except (OSError, ValueError):
        return ""


def jellyfin_index(cle):
    """Nom de fichier -> identifiant Jellyfin, pour tous les films et épisodes (gardé 5 minutes)."""
    if time.time() - JELLYFIN_URL["t"] < 300 and JELLYFIN_URL["index"]:
        return JELLYFIN_URL["index"]
    r = httpx.get(f"http://127.0.0.1:{tools.PORTS['jellyfin']}/Items", timeout=30, headers={"X-Emby-Token": cle},
                  params={"Recursive": "true", "IncludeItemTypes": "Movie,Episode,Video", "Fields": "Path",
                          "EnableImages": "false", "EnableUserData": "false"})
    if r.status_code in (401, 403):
        raise HTTPException(409, {"cle_jellyfin": True, "message": "La clé Jellyfin n'est plus valable."})
    r.raise_for_status()
    JELLYFIN_URL["index"] = {os.path.basename(x.get("Path") or ""): x["Id"] for x in r.json().get("Items", []) if x.get("Path")}
    JELLYFIN_URL["t"] = time.time()
    return JELLYFIN_URL["index"]


@router.get("/api/jellyfin/trouver")
def find_in_jellyfin(chemin: str):
    lien = config.LINKS.get("jellyfin")
    if not lien:
        raise HTTPException(404, "Jellyfin n'a pas d'adresse dans les réglages de l'appli (LIENS).")
    cle = jellyfin_key()
    if not cle:
        raise HTTPException(409, {"cle_jellyfin": True, "message": "Jabba n'est pas encore relié à Jellyfin."})
    try:
        ident = jellyfin_index(cle).get(chemin.rsplit("/", 1)[-1])
    except httpx.HTTPError:
        raise HTTPException(502, "Jellyfin ne répond pas : vérifie qu'il est activé dans l'Établi.")
    if not ident:
        raise HTTPException(404, "Ce fichier n'est pas dans une médiathèque Jellyfin (ou Jellyfin ne l'a pas encore analysé).")
    return {"lien": f"{lien.rstrip('/')}/web/#/details?id={ident}"}


@router.post("/api/jellyfin/cle")
async def save_jellyfin_key(request: Request):
    j = guard.require_admin(request, fort=True)
    cle = str((await request.json()).get("cle", "")).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", cle):
        raise HTTPException(400, "Une clé Jellyfin fait 32 caractères (chiffres et lettres de a à f).")
    try:
        JELLYFIN_URL["t"] = 0
        jellyfin_index(cle)
    except HTTPException:
        raise HTTPException(400, "Jellyfin refuse cette clé.")
    except httpx.HTTPError:
        raise HTTPException(502, "Jellyfin ne répond pas : vérifie qu'il est activé dans l'Établi.")
    with open(JELLYFIN_KEY_FILE, "w") as f:
        json.dump({"cle": cle}, f)
    os.chmod(JELLYFIN_KEY_FILE, 0o600)
    activity_log.record("admin", f"Jabba relié à Jellyfin, par {j['identifiant']}", request.state.nom)
    return {"ok": True}


@router.get("/api/lecture/infos")
def playback_info(chemin: str):
    infos = media_info(chemin)
    infos["directe"] = video_player.plays_directly(infos)
    return infos


@router.post("/api/lecture/demarrer")
async def start_playback(request: Request):
    corps = await request.json()
    chemin = corps.get("chemin", "")
    infos = await asyncio.to_thread(media_info, chemin)
    if not infos["video"]:
        raise HTTPException(415, "Pas d'image dans ce fichier.")
    if corps.get("remplace"):
        video_player.stop(str(corps["remplace"]))
    sid, debut = await asyncio.to_thread(video_player.start, playback_source(chemin), infos,
                                         float(corps.get("debut") or 0), corps.get("audio"))
    return {"id": sid, "url": f"/api/lecture/{sid}/index.m3u8", "duree": infos["duree"], "debut": debut}


@router.get("/api/lecture/soustitres")
def playback_subtitles(chemin: str, piste: int, debut: float = 0):
    try:
        vtt = video_player.subtitles(playback_source(chemin), piste, debut)
    except (RuntimeError, subprocess.TimeoutExpired):
        raise HTTPException(415, "Sous-titres illisibles.")
    return Response(vtt, media_type="text/vtt; charset=utf-8")


@router.get("/api/lecture/{sid}/{nom}")
def playback_segment(sid: str, nom: str):
    chemin = video_player.session_file(sid, nom)
    if not chemin:
        raise HTTPException(404 if video_player.session_error(sid) is None else 500, video_player.session_error(sid) or "Pas encore prêt.")
    if nom.endswith(".m3u8"):
        # Lecture depuis le début de la liste (et pas « en direct », au dernier morceau produit)
        texte = open(chemin).read().replace("#EXTM3U\n", "#EXTM3U\n#EXT-X-START:TIME-OFFSET=0,PRECISE=YES\n", 1)
        return Response(texte, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})
    return FileResponse(chemin, media_type="video/mp2t", headers={"Cache-Control": "no-store"})


@router.post("/api/lecture/{sid}/arreter")
def stop_playback(sid: str):
    video_player.stop(sid)
    return {"ok": True}
