"""API des actions groupées de l'écran Fichiers : télécharger en .zip, annuler une suppression.

Sommaire :
  GET  /api/fichiers/zip                    download_zip (plusieurs fichiers ou dossiers)
  POST /api/fichiers/annuler-suppression    undo_delete (bouton « Annuler » après une suppression)
"""

import os

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from services import activity_log, trash, zip_download

router = APIRouter()


@router.get("/api/fichiers/zip")
async def download_zip(request: Request, chemin: list[str] = Query(default=[])):
    output_path, name = await zip_download.build_zip(chemin)
    count = len(chemin)
    activity_log.record(
        "fichier", f"Téléchargement groupé : {count} élément{'s' if count > 1 else ''} ({name})", request.state.nom
    )
    return FileResponse(
        output_path, filename=name, media_type="application/zip", background=BackgroundTask(os.unlink, output_path)
    )


@router.post("/api/fichiers/annuler-suppression")
async def undo_delete(request: Request):
    paths = (await request.json()).get("paths") or []
    if not isinstance(paths, list) or not paths:
        raise HTTPException(400, "Rien à annuler.")
    restored = await trash.undo_delete([str(path) for path in paths])
    if not restored:
        raise HTTPException(404, "Trop tard : retrouve ces éléments dans la Corbeille.")
    activity_log.record(
        "fichier",
        f"Suppression annulée : {len(restored)} élément{'s' if len(restored) > 1 else ''} remis en place",
        request.state.nom,
    )
    return {"restored": restored}
