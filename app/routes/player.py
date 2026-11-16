"""API du lecteur vidéo : conversion à la volée, sous-titres, et Jellyfin.

Sommaire :
  outils : playback_source, media_info, jellyfin_key, jellyfin_index
  GET  /api/jellyfin/trouver         find_in_jellyfin
  POST /api/jellyfin/cle             save_jellyfin_key
  GET  /api/lecture/infos            playback_info
  POST /api/lecture/demarrer         start_playback
  GET  /api/lecture/soustitres       playback_subtitles
  GET  /api/lecture/{sid}/{nom}      playback_segment
  POST /api/lecture/{sid}/arreter    stop_playback
"""

import asyncio
import base64
import json
import os
import re
import subprocess
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

import config
from security import guard
from services import activity_log, disk_browser, files_source, nextcloud_files, tools, video_player

router = APIRouter()


# ---------------------------------------------------------------- lecteur vidéo (conversion à la volée)
def playback_source(path):
    """Où ffmpeg lit le fichier : sur le disque si possible, sinon par Nextcloud."""
    path = nextcloud_files.clean_path(path)
    if files_source.files_source() == "disque":
        return {
            "entree": files_source.run_disk_action(lambda: disk_browser.read_path(path, *files_source.disk_params()))
        }
    first, _, rest = path.strip("/").partition("/")
    known = files_source.known_disks()
    if first in known:  # un disque externe : lu directement, sans passer par Nextcloud
        base = os.path.realpath(known[first])
        real_path = os.path.realpath(os.path.join(base, rest))
        if (real_path == base or real_path.startswith(base + os.sep)) and os.path.isfile(real_path):
            return {"entree": real_path}
    nextcloud_files.require_nextcloud()
    token = base64.b64encode(f"{config.NC_USER}:{config.NC_APP_PASSWORD}".encode()).decode()
    return {"entree": nextcloud_files.dav_url(path), "entetes": f"Authorization: Basic {token}\r\n"}


_MEDIA_INFO_CACHE = {}  # chemin -> (moment, infos) : l'analyse n'est refaite qu'une fois par film (10 min)


def media_info(path):
    already = _MEDIA_INFO_CACHE.get(path)
    if already and time.time() - already[0] < 600:
        return dict(already[1])
    try:
        media = video_player.probe(playback_source(path))
    except RuntimeError as error:
        raise HTTPException(415, str(error))
    except FileNotFoundError:
        raise HTTPException(500, "Le moteur vidéo (ffmpeg) manque dans l'appli.")
    _MEDIA_INFO_CACHE[path] = (time.time(), media)
    return dict(media)


# ---------------------------------------------------------------- Jellyfin : ouvrir un film depuis Fichiers
JELLYFIN_URL = {"t": 0, "index": {}}
JELLYFIN_KEY_FILE = os.path.join(os.environ.get("JABBA_DATA", "/data"), "jellyfin.json")


def jellyfin_key():
    try:
        with open(JELLYFIN_KEY_FILE) as file:
            return json.load(file).get("cle") or ""
    except (OSError, ValueError):
        return ""


def jellyfin_index(key):
    """Nom de fichier -> identifiant Jellyfin, pour tous les films et épisodes (gardé 5 minutes)."""
    if time.time() - JELLYFIN_URL["t"] < 300 and JELLYFIN_URL["index"]:
        return JELLYFIN_URL["index"]
    response = httpx.get(
        f"http://127.0.0.1:{tools.PORTS['jellyfin']}/Items",
        timeout=30,
        headers={"X-Emby-Token": key},
        params={
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Episode,Video",
            "Fields": "Path",
            "EnableImages": "false",
            "EnableUserData": "false",
        },
    )
    if response.status_code in (401, 403):
        raise HTTPException(409, {"cle_jellyfin": True, "message": "La clé Jellyfin n'est plus valable."})
    response.raise_for_status()
    JELLYFIN_URL["index"] = {
        os.path.basename(item.get("Path") or ""): item["Id"]
        for item in response.json().get("Items", [])
        if item.get("Path")
    }
    JELLYFIN_URL["t"] = time.time()
    return JELLYFIN_URL["index"]


@router.get("/api/jellyfin/trouver")
def find_in_jellyfin(chemin: str):
    link = config.LINKS.get("jellyfin")
    if not link:
        raise HTTPException(404, "Jellyfin n'a pas d'adresse dans les réglages de l'appli (LIENS).")
    key = jellyfin_key()
    if not key:
        raise HTTPException(409, {"cle_jellyfin": True, "message": "Jabba n'est pas encore relié à Jellyfin."})
    try:
        identifier = jellyfin_index(key).get(chemin.rsplit("/", 1)[-1])
    except httpx.HTTPError:
        raise HTTPException(502, "Jellyfin ne répond pas : vérifie qu'il est activé dans l'Établi.")
    if not identifier:
        raise HTTPException(
            404, "Ce fichier n'est pas dans une médiathèque Jellyfin (ou Jellyfin ne l'a pas encore analysé)."
        )
    return {"lien": f"{link.rstrip('/')}/web/#/details?id={identifier}"}


@router.post("/api/jellyfin/cle")
async def save_jellyfin_key(request: Request):
    session = guard.require_admin(request, strong=True)
    key = str((await request.json()).get("cle", "")).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", key):
        raise HTTPException(400, "Une clé Jellyfin fait 32 caractères (chiffres et lettres de a à f).")
    try:
        JELLYFIN_URL["t"] = 0
        jellyfin_index(key)
    except HTTPException:
        raise HTTPException(400, "Jellyfin refuse cette clé.")
    except httpx.HTTPError:
        raise HTTPException(502, "Jellyfin ne répond pas : vérifie qu'il est activé dans l'Établi.")
    with open(JELLYFIN_KEY_FILE, "w") as file:
        json.dump({"cle": key}, file)
    os.chmod(JELLYFIN_KEY_FILE, 0o600)
    activity_log.record("admin", f"Jabba relié à Jellyfin, par {session['identifiant']}", request.state.nom)
    return {"ok": True}


@router.get("/api/lecture/infos")
def playback_info(chemin: str):
    media = media_info(chemin)
    media["directe"] = video_player.plays_directly(media)
    return media


@router.post("/api/lecture/demarrer")
async def start_playback(request: Request):
    body = await request.json()
    path = body.get("chemin", "")
    media = await asyncio.to_thread(media_info, path)
    if not media["video"]:
        raise HTTPException(415, "Pas d'image dans ce fichier.")
    if body.get("remplace"):
        video_player.stop(str(body["remplace"]))
    session_id, start = await asyncio.to_thread(
        video_player.start, playback_source(path), media, float(body.get("debut") or 0), body.get("audio")
    )
    return {"id": session_id, "url": f"/api/lecture/{session_id}/index.m3u8", "duree": media["duree"], "debut": start}


@router.get("/api/lecture/soustitres")
def playback_subtitles(chemin: str, piste: int, debut: float = 0):
    try:
        vtt_text = video_player.subtitles(playback_source(chemin), piste, debut)
    except (RuntimeError, subprocess.TimeoutExpired):
        raise HTTPException(415, "Sous-titres illisibles.")
    return Response(vtt_text, media_type="text/vtt; charset=utf-8")


@router.get("/api/lecture/{sid}/{nom}")
def playback_segment(sid: str, nom: str):
    path = video_player.session_file(sid, nom)
    if not path:
        raise HTTPException(
            404 if video_player.session_error(sid) is None else 500,
            video_player.session_error(sid) or "Pas encore prêt.",
        )
    if nom.endswith(".m3u8"):
        # Lecture depuis le début de la liste (et pas « en direct », au dernier morceau produit)
        text = open(path).read().replace("#EXTM3U\n", "#EXTM3U\n#EXT-X-START:TIME-OFFSET=0,PRECISE=YES\n", 1)
        return Response(text, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})
    return FileResponse(path, media_type="video/mp2t", headers={"Cache-Control": "no-store"})


@router.post("/api/lecture/{sid}/arreter")
def stop_playback(sid: str):
    video_player.stop(sid)
    return {"ok": True}
