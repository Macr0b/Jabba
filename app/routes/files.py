"""API de l'écran Fichiers : parcourir, envoyer, télécharger, voir, rechercher, corbeille.

Les fichiers viennent de Nextcloud, ou directement des disques du serveur quand
Nextcloud est arrêté (services/files_source.py choisit).

Sommaire :
  1. dossiers        list_folder, home_folders, toggle_favorite, create_folder
  2. fichiers        upload_files, download_file, move_item, delete_item
  3. visionneuse     viewable_type, viewer_headers, thumbnail, view_file
  4. recherche       search
  5. corbeille       list_trash, restore_from_trash, erase_from_trash
"""

import asyncio
import mimetypes
import os
import time
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.datastructures import UploadFile

import config
from security import guard
from services import activity_log, disk_browser, files_source, home_data, nextcloud_files, safe_files, trash

router = APIRouter()


@router.get("/api/fichiers")
async def list_folder(chemin: str = "/", visite: int = 1):
    """`visite=0` : simple coup d'œil (sélecteur de dossier), sans compter dans les dossiers récents."""
    chemin = nextcloud_files.clean_path(chemin)
    if files_source.files_source() == "disque":
        listing = files_source.run_disk_action(lambda: disk_browser.list_folder(chemin, *files_source.disk_params()))
    else:
        nextcloud_files.require_nextcloud()
        listing = await nextcloud_files.read_folder(chemin)
        listing["source"] = "nextcloud"
    if chemin != "/" and visite:
        home_data.record_visit(chemin)
    listing["favori"] = chemin in home_data.read()["favoris"]
    return listing


@router.get("/api/accueil/dossiers")
async def home_folders():
    if files_source.files_source() == "disque":
        root = files_source.run_disk_action(lambda: disk_browser.list_folder("/", *files_source.disk_params()))
    else:
        nextcloud_files.require_nextcloud()
        root = await nextcloud_files.read_folder("/")
    plugged, known = files_source.plugged_disks(), files_source.known_disks()
    essentials = [{"nom": "Mes fichiers", "chemin": "/", "type": "racine"}]
    for entry in root["elements"]:
        if files_source.files_source() == "disque" and entry["dossier"]:
            essentials.append(
                {"nom": entry["nom"], "chemin": entry["chemin"], "type": "disque" if entry["externe"] else "espace"}
            )
        elif entry["dossier"] and entry["externe"]:
            if entry["nom"] in known:
                if entry["nom"] in plugged:
                    essentials.append({"nom": entry["nom"], "chemin": entry["chemin"], "type": "disque"})
            else:
                essentials.append({"nom": entry["nom"], "chemin": entry["chemin"], "type": "espace"})
    listing = home_data.read()
    favorites = [{"nom": dav_client.rsplit("/", 1)[-1], "chemin": dav_client} for dav_client in listing["favoris"]]
    cutoff = time.time() - home_data.SUGGESTION_WINDOW
    suggestions = []
    for dav_client, timestamps in listing["visites"].items():
        count = sum(1 for media_type in timestamps if media_type > cutoff)
        if (
            count >= home_data.SUGGESTION_THRESHOLD
            and dav_client not in listing["favoris"]
            and dav_client not in listing["ignores"]
        ):
            suggestions.append({"nom": dav_client.rsplit("/", 1)[-1], "chemin": dav_client, "ouvertures": count})
    suggestions.sort(key=lambda source_name: -source_name["ouvertures"])
    return {"essentiels": essentials, "favoris": favorites, "suggestions": suggestions[:2]}


@router.post("/api/favoris")
async def toggle_favorite(request: Request):
    body = await request.json()
    path = nextcloud_files.clean_path(body.get("chemin", ""))
    action = body.get("action")
    if path == "/" or action not in ("ajouter", "retirer", "ignorer"):
        raise HTTPException(400, "Demande invalide")
    with home_data.LOCK:
        listing = home_data.read()
        if action == "ajouter" and path not in listing["favoris"]:
            listing["favoris"].append(path)
        elif action == "retirer":
            listing["favoris"] = [dav_client for dav_client in listing["favoris"] if dav_client != path]
        elif action == "ignorer" and path not in listing["ignores"]:
            listing["ignores"].append(path)
        home_data.write(listing)
    return {"ok": True, "favori": path in listing["favoris"]}


@router.post("/api/fichiers/dossier")
async def create_folder(request: Request):
    body = await request.json()
    path = nextcloud_files.clean_path(body.get("chemin", ""))
    if files_source.files_source() == "disque":
        files_source.run_disk_action(lambda: disk_browser.create_folder(path, *files_source.disk_params()))
        return {"ok": True}
    nextcloud_files.require_nextcloud()
    if config.DEMO:
        return {"ok": True}
    async with nextcloud_files.client() as dav_client:
        response = await dav_client.request("MKCOL", nextcloud_files.dav_url(path))
    if response.status_code == 405:
        raise HTTPException(409, "Un dossier porte déjà ce nom.")
    await nextcloud_files.check_response(response, "Création du dossier")
    return {"ok": True}


@router.post("/api/fichiers/envoyer")
async def upload_files(request: Request, chemin: str = "/"):
    chemin = nextcloud_files.clean_path(chemin)
    form = await request.form()
    uploaded = []
    if files_source.files_source() == "disque":
        params = files_source.disk_params()
        for upload in form.getlist("fichiers"):
            if not isinstance(upload, UploadFile) or not upload.filename:
                continue
            name = os.path.basename(upload.filename.replace("\\", "/"))
            real_path = files_source.run_disk_action(
                lambda file_name=name: disk_browser.upload_path(chemin, file_name, *params)
            )
            try:
                output = safe_files.open_new_file(real_path)
            except FileExistsError:
                raise HTTPException(409, f"« {name} » existe déjà dans ce dossier.")
            with output:
                while chunk := await upload.read(safe_files.CHUNK_SIZE):
                    output.write(chunk)
            disk_browser.finish_upload(real_path)
            uploaded.append(name)
        log_upload(request, chemin, uploaded)
        return {"envoyes": uploaded}
    nextcloud_files.require_nextcloud()
    for upload in form.getlist("fichiers"):
        if not isinstance(upload, UploadFile) or not upload.filename:
            continue
        name = os.path.basename(upload.filename.replace("\\", "/"))
        if config.DEMO:
            uploaded.append(name)
            continue

        async def morceaux(file_path=upload):
            while chunk := await file_path.read(1024 * 1024):
                yield chunk

        async with nextcloud_files.client() as dav_client:
            response = await dav_client.put(nextcloud_files.dav_url(f"{chemin}/{name}"), content=morceaux())
        await nextcloud_files.check_response(response, f"Envoi de {name}")
        uploaded.append(name)
    log_upload(request, chemin, uploaded)
    return {"envoyes": uploaded}


def log_upload(request, path, uploaded):
    if uploaded:
        what = f"« {uploaded[0]} »" if len(uploaded) == 1 else f"{len(uploaded)} fichiers"
        activity_log.record(
            "fichier", f"{what} envoyé{'s' if len(uploaded) > 1 else ''} dans {path}", request.state.nom
        )


@router.get("/api/fichiers/telecharger")
async def download_file(chemin: str):
    chemin = nextcloud_files.clean_path(chemin)
    if files_source.files_source() == "disque":
        real_path = files_source.run_disk_action(lambda: disk_browser.read_path(chemin, *files_source.disk_params()))
        return FileResponse(real_path, filename=os.path.basename(real_path))
    nextcloud_files.require_nextcloud()
    if config.DEMO:
        raise HTTPException(404, "Pas de fichiers en démo")
    dav_client = nextcloud_files.client()
    request_object = dav_client.build_request("GET", nextcloud_files.dav_url(chemin))
    response = await dav_client.send(request_object, stream=True)
    if response.status_code >= 400:
        await response.aclose()
        await dav_client.aclose()
        raise HTTPException(response.status_code, "Fichier introuvable")
    name = chemin.rsplit("/", 1)[-1]

    async def flux():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await dav_client.aclose()

    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"}
    if "content-length" in response.headers:
        headers["Content-Length"] = response.headers["content-length"]
    return StreamingResponse(
        flux(), media_type=response.headers.get("content-type", "application/octet-stream"), headers=headers
    )


@router.post("/api/fichiers/deplacer")
async def move_item(request: Request):
    body = await request.json()
    source_path, destination = (
        nextcloud_files.clean_path(body.get("de", "")),
        nextcloud_files.clean_path(body.get("vers", "")),
    )
    if source_path == "/" or destination == "/":
        raise HTTPException(400, "Chemin invalide")
    if files_source.files_source() == "disque":
        files_source.run_disk_action(lambda: disk_browser.move(source_path, destination, *files_source.disk_params()))
        return {"ok": True}
    nextcloud_files.require_nextcloud()
    if config.DEMO:
        return {"ok": True}
    async with nextcloud_files.client() as dav_client:
        response = await dav_client.request(
            "MOVE",
            nextcloud_files.dav_url(source_path),
            headers={"Destination": nextcloud_files.dav_url(destination), "Overwrite": "F"},
        )
    await nextcloud_files.check_response(response, "Renommage")
    return {"ok": True}


@router.post("/api/fichiers/supprimer")
async def delete_item(request: Request):
    body = await request.json()
    path = nextcloud_files.clean_path(body.get("chemin", ""))
    if (
        path == "/"
        or len([parts_list for parts_list in path.split("/") if parts_list]) < 2
        and files_source.files_source() == "disque"
    ):
        raise HTTPException(400, "Ce dossier ne peut pas être supprimé.")
    if files_source.files_source() == "disque":
        params = files_source.disk_params()
        if files_source.run_disk_action(lambda: disk_browser.is_folder(path, *params)):
            guard.require_admin(request)  # supprimer un dossier entier demande le mot de passe
        files_source.run_disk_action(lambda: disk_browser.delete(path, *params))
        activity_log.record("fichier", f"{path} mis à la corbeille", request.state.nom)
        return {"ok": True}
    nextcloud_files.require_nextcloud()
    if config.DEMO:
        if body.get("dossier"):
            guard.require_admin(request)
        return {"ok": True}
    if await nextcloud_files.is_nextcloud_folder(path):
        guard.require_admin(request)
    async with nextcloud_files.client() as dav_client:
        response = await dav_client.delete(nextcloud_files.dav_url(path))
    await nextcloud_files.check_response(response, "Suppression")
    activity_log.record("fichier", f"{path} mis à la corbeille", request.state.nom)
    return {"ok": True}


# ---------------------------------------------------------------- aperçus et visionneuse
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


@router.get("/api/recherche")
async def search(q: str = ""):
    """Cherche par nom, partout : dans Nextcloud et directement sur les disques du serveur."""
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(400, "Tape au moins 2 lettres.")
    if config.DEMO and files_source.files_source() != "disque":
        from services.demo_data import search_results

        return search_results(q)
    disks, read_only = files_source.disk_params()
    if files_source.files_source() == "disque":
        result, full_command = await asyncio.to_thread(disk_browser.search, q, disks, read_only)
        return {"elements": result, "complet": full_command}
    nextcloud_files.require_nextcloud()
    async with nextcloud_files.client() as dav_client:
        response = await dav_client.request(
            "SEARCH",
            config.NC_URL + "/remote.php/dav/",
            headers={"Content-Type": "text/xml"},
            content=nextcloud_files.SEARCH.format(user=xml_escape(config.NC_USER), texte=xml_escape(q)).encode(),
        )
    result = []
    if response.status_code < 400:
        for rep in nextcloud_files.parse_xml(response.content).findall("d:response", nextcloud_files.DAV_NS):
            if rep.find("d:propstat/d:prop", nextcloud_files.DAV_NS) is not None:
                result.append(nextcloud_files.dav_entry(rep))
    # Les dossiers du serveur vus dans Nextcloud (films, disques…) ne sont pas
    # tous indexés par Nextcloud : on les parcourt directement.
    root = await nextcloud_files.read_folder("/")
    external_disks = {entry["nom"] for entry in root["elements"] if entry["externe"]}
    direct_play, full_command = await asyncio.to_thread(disk_browser.search, q, disks, read_only, external_disks)
    seen = {entry["chemin"] for entry in result}
    result += [entry for entry in direct_play if entry["chemin"] not in seen]
    result.sort(
        key=lambda entry: (not entry["nom"].lower().startswith(q.lower()), not entry["dossier"], entry["nom"].lower())
    )
    return {"elements": result[:100], "complet": full_command and len(result) <= 100}


@router.get("/api/corbeille")
async def list_trash():
    return {"elements": await trash.list_entries(), "garde": 30}


@router.post("/api/corbeille/restaurer")
async def restore_from_trash(request: Request):
    body = await request.json()
    path = await trash.restore_entry(str(body.get("id", "")), body.get("source"), body.get("origine"))
    activity_log.record("fichier", f"{path} restauré depuis la corbeille", request.state.nom)
    return {"ok": True, "chemin": path}


@router.post("/api/corbeille/effacer")
async def erase_from_trash(request: Request):
    """Effacer pour de bon : un élément, ou toute la corbeille. Demande le mot de passe du serveur."""
    guard.require_admin(request, strong=True)
    body = await request.json()
    identifier, source_path, everything = str(body.get("id", "")), body.get("source"), bool(body.get("tout"))
    if config.DEMO and files_source.files_source() != "disque":
        return {"ok": True}
    if everything:
        await asyncio.to_thread(disk_browser.empty_trash)
        if files_source.files_source() == "nextcloud" and config.NC_USER:
            async with nextcloud_files.client() as dav_client:
                await dav_client.delete(nextcloud_files.trash_url())
        activity_log.record("fichier", "Corbeille vidée", request.state.nom)
        return {"ok": True}
    if source_path == "disque":
        files_source.run_disk_action(lambda: disk_browser.erase(identifier))
    else:
        nextcloud_files.require_nextcloud()
        if "/" in identifier:
            raise HTTPException(400, "Élément invalide")
        async with nextcloud_files.client() as dav_client:
            response = await dav_client.delete(nextcloud_files.trash_url(identifier))
        await nextcloud_files.check_response(response, "Effacement")
    activity_log.record("fichier", f"« {body.get('nom', identifier)} » effacé définitivement", request.state.nom)
    return {"ok": True}
