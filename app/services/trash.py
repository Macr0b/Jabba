"""Corbeille commune aux deux sources de fichiers (disques du serveur, Nextcloud).

Sommaire
- list_entries()                          : tout ce qui est dans la corbeille, le plus récent d'abord.
- restore_entry(identifier, source, origin): remet un élément à sa place ; renvoie son chemin.
- original_path(entry)                    : le chemin qu'avait l'élément avant d'être supprimé.
- undo_delete(paths)                      : bouton « Annuler » après une suppression.
"""

import asyncio
import time
from urllib.parse import quote

from fastapi import HTTPException

import config
from services import disk_browser, files_source, nextcloud_files

UNDO_WINDOW_SECONDS = 10 * 60


async def list_entries():
    if config.DEMO and files_source.files_source() != "disque":
        from services.demo_data import TRASH

        return list(TRASH)
    entries = await asyncio.to_thread(disk_browser.list_trash)
    if files_source.files_source() == "nextcloud":
        nextcloud_files.require_nextcloud()
        entries = await nextcloud_files.nextcloud_trash() + entries
    entries.sort(key=lambda entry: -entry["supprime"])
    return entries


async def restore_entry(identifier, source, origin):
    if config.DEMO and source != "disque":
        return "/Documents"
    if source == "disque":
        return files_source.run_disk_action(lambda: disk_browser.restore(identifier, *files_source.disk_params()))
    nextcloud_files.require_nextcloud()
    if "/" in identifier:
        raise HTTPException(400, "Élément invalide")
    async with nextcloud_files.client() as dav_client:
        response = await dav_client.request(
            "MOVE",
            nextcloud_files.trash_url(identifier),
            headers={
                "Destination": config.NC_URL
                + f"/remote.php/dav/trashbin/{quote(config.NC_USER)}/restore/{quote(identifier, safe='')}"
            },
        )
    await nextcloud_files.check_response(response, "Restauration")
    return origin or "/"


def original_path(entry):
    """Disque : « origine » est le dossier parent. Nextcloud : c'est déjà le chemin complet."""
    if entry.get("source") == "nextcloud":
        return entry.get("origine") or ""
    parent = (entry.get("origine") or "").rstrip("/")
    return f"{parent}/{entry['nom']}" if entry.get("origine") else ""


async def undo_delete(paths, now=None):
    """Restaure les éléments supprimés il y a moins de 10 minutes à ces chemins. Renvoie les chemins remis."""
    now = time.time() if now is None else now
    wanted = {path.rstrip("/") for path in paths if path}
    entries = await list_entries()
    restored = []
    for entry in entries:  # le plus récent d'abord : en cas de doublon, on reprend la dernière suppression
        path = original_path(entry)
        if path not in wanted or now - entry["supprime"] > UNDO_WINDOW_SECONDS:
            continue
        wanted.discard(path)
        restored.append(await restore_entry(entry["id"], entry["source"], entry.get("origine")))
    return restored
