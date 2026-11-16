"""API de la visionneuse : aperçus (vignettes) et lecture d'un fichier dans le navigateur.

Sommaire :
  VIEWABLE_TYPES, TEXT_EXTENSIONS : ce que la visionneuse sait montrer
  viewable_type, viewer_headers   : type et en-têtes d'un fichier à montrer
  thumbnail                       GET /api/fichiers/apercu
  view_file                       GET /api/fichiers/voir
"""

import asyncio
import mimetypes
import os
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

import config
from services import disk_browser, files_source, nextcloud_files

router = APIRouter()


VIEWABLE_TYPES = ("image/", "video/", "audio/", "application/pdf", "text/plain")
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".log",
    ".json",
    ".yaml",
    ".yml",
    ".ini",
    ".conf",
    ".srt",
    ".nfo",
    ".py",
    ".sh",
}
mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("video/x-matroska", ".mkv")


def viewable_type(name):
    """Type à donner au navigateur pour l'afficher, ou None (fichier à télécharger)."""
    extension = os.path.splitext(name)[1].lower()
    if extension in TEXT_EXTENSIONS:
        return "text/plain; charset=utf-8"
    media_type = mimetypes.guess_type(name)[0] or ""
    return media_type if media_type.startswith(VIEWABLE_TYPES) else None


def viewer_headers(media_type, name):
    entry = {
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}",
        "X-Frame-Options": "SAMEORIGIN",
        "Cache-Control": "private, max-age=300",
    }
    if media_type != "application/pdf":
        # Une image SVG ou un texte ouvert seul ne peut lancer aucun script.
        entry["Content-Security-Policy"] = (
            "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"
        )
    return entry


@router.get("/api/fichiers/apercu")
async def thumbnail(chemin: str, taille: int = 128):
    chemin = nextcloud_files.clean_path(chemin)
    taille = 256 if taille > 128 else 128
    if config.DEMO and files_source.files_source() != "disque":
        raise HTTPException(404)
    if files_source.files_source() == "disque":
        real_path = files_source.run_disk_action(lambda: disk_browser.read_path(chemin, *files_source.disk_params()))
        try:
            cache = await asyncio.to_thread(disk_browser.thumbnail, real_path, taille)
        except disk_browser.Refusal as error:
            raise HTTPException(error.code, error.message)
        except Exception:  # noqa: BLE001 — image abîmée ou format inconnu
            raise HTTPException(404, "Pas d'aperçu")
        return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
    nextcloud_files.require_nextcloud()
    async with nextcloud_files.client() as dav_client:
        response = await dav_client.get(
            config.NC_URL + "/index.php/core/preview.png",
            params={"file": chemin, "x": taille, "y": taille, "a": "1", "mode": "cover"},
        )
    if response.status_code != 200 or not response.headers.get("content-type", "").startswith("image/"):
        raise HTTPException(404, "Pas d'aperçu")
    return Response(
        response.content,
        media_type=response.headers["content-type"],
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/api/fichiers/voir")
async def view_file(chemin: str, request: Request):
    """Le fichier lui-même, à afficher dans l'appli (photos, PDF, vidéos, musique, textes)."""
    chemin = nextcloud_files.clean_path(chemin)
    name = chemin.rsplit("/", 1)[-1]
    media_type = viewable_type(name)
    if not media_type:
        raise HTTPException(415, "Ce type de fichier ne s'affiche pas dans l'appli : télécharge-le.")
    if config.DEMO and files_source.files_source() != "disque":
        raise HTTPException(404, "Pas de fichiers en démo")
    if files_source.files_source() == "disque":
        real_path = files_source.run_disk_action(lambda: disk_browser.read_path(chemin, *files_source.disk_params()))
        return FileResponse(real_path, media_type=media_type, headers=viewer_headers(media_type, name))
    nextcloud_files.require_nextcloud()
    dav_client = nextcloud_files.client()
    headers = {"Range": request.headers["range"]} if "range" in request.headers else {}
    response = await dav_client.send(
        dav_client.build_request("GET", nextcloud_files.dav_url(chemin), headers=headers), stream=True
    )
    if response.status_code >= 400:
        await response.aclose()
        await dav_client.aclose()
        raise HTTPException(404 if response.status_code == 404 else 502, "Fichier introuvable")

    async def flux():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await dav_client.aclose()

    output = viewer_headers(media_type, name)
    for header in ("content-length", "content-range", "accept-ranges", "etag", "last-modified"):
        if header in response.headers:
            output[header] = response.headers[header]
    output.setdefault("accept-ranges", "bytes")
    return StreamingResponse(flux(), status_code=response.status_code, media_type=media_type, headers=output)
