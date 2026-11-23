"""API de l'écran Fichiers : parcourir, envoyer, télécharger, déplacer, supprimer, rechercher.

Les fichiers viennent de Nextcloud, ou directement des disques du serveur quand
Nextcloud est arrêté (services/files_source.py choisit).

Sommaire :
  1. dossiers        list_folder, home_folders, toggle_favorite, create_folder
  2. fichiers        upload_files, download_file, move_item, delete_item
  3. recherche       search
  (visionneuse : routes/files_viewer.py ; corbeille : routes/trash.py ; actions groupées : routes/files_batch.py)
  log_upload : note au Journal les fichiers envoyés
"""

import asyncio
import os
import time
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.datastructures import UploadFile

import config
from security import guard
from services import activity_log, disk_browser, files_source, home_data, nextcloud_files, safe_files

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
