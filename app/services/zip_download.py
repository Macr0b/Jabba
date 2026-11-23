"""Télécharger plusieurs fichiers ou dossiers d'un coup, en un seul fichier .zip.

Sommaire
- build_zip(paths) : crée le .zip dans un fichier temporaire ; renvoie (chemin du .zip, nom proposé).

Le .zip est « stocké » (sans compression) : photos et vidéos sont déjà
compressées, et le serveur reste rapide. On vérifie la taille avant de commencer :
au plus JABBA_ZIP_MAX_GO (20 Go par défaut), et jamais au point de remplir le disque.
Sur les disques, les liens symboliques sont ignorés : un lien ne fait pas sortir du dossier choisi.
  interne : _zip_name, _check_limits, _disk_files, _write_disk_zip, _nextcloud_files, _write_nextcloud_zip
"""

import asyncio
import os
import tempfile
import zipfile

import config
from services import disk_browser, files_source, nextcloud_files, safe_files
from services.service_error import ServiceError

MAX_ZIP_BYTES = int(float(os.environ.get("JABBA_ZIP_MAX_GO", "20")) * 1024**3)
KEEP_FREE_BYTES = 5 * 1024**3
MAX_FILES = 20000


def _zip_name(paths):
    if len(paths) == 1:
        return (paths[0].rstrip("/").rsplit("/", 1)[-1] or "Fichiers") + ".zip"
    parent = paths[0].rstrip("/").rsplit("/", 1)[0].rsplit("/", 1)[-1] or "Fichiers"
    return f"{parent} ({len(paths)} éléments).zip"


def _check_limits(count, total_bytes, folder):
    if count > MAX_FILES:
        raise ServiceError(413, f"Trop de fichiers d'un coup ({count}) : choisis-en moins de {MAX_FILES}.")
    if total_bytes > MAX_ZIP_BYTES:
        raise ServiceError(413, f"Trop gros pour un seul téléchargement (plus de {MAX_ZIP_BYTES // 1024**3} Go).")
    if safe_files.free_space(folder) - total_bytes < KEEP_FREE_BYTES:
        raise ServiceError(507, "Pas assez de place libre sur le serveur pour préparer ce téléchargement.")


# ---------------------------------------------------------------- disques du serveur
def _disk_files(paths):
    """(chemin réel, nom dans le .zip, taille) de chaque fichier, sans suivre les liens."""
    params = files_source.disk_params()
    found = []
    for path in paths:
        real_path, _ = files_source.run_disk_action(lambda path=path: disk_browser.resolve(path, *params))
        if real_path is None or os.path.islink(real_path) or not os.path.exists(real_path):
            raise ServiceError(404, f"Introuvable : {path}")
        base = os.path.dirname(real_path)
        if os.path.isfile(real_path):
            found.append((real_path, os.path.basename(real_path), os.path.getsize(real_path)))
            continue
        for folder, subfolders, names in os.walk(real_path):
            subfolders[:] = [name for name in subfolders if not os.path.islink(os.path.join(folder, name))]
            for name in names:
                file_path = os.path.join(folder, name)
                if os.path.islink(file_path) or not os.path.isfile(file_path):
                    continue
                found.append((file_path, os.path.relpath(file_path, base), os.path.getsize(file_path)))
    return found


def _write_disk_zip(files, output_path):
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        for real_path, name, _ in files:
            archive.write(real_path, name)


# ---------------------------------------------------------------- Nextcloud
async def _nextcloud_files(paths):
    found = []

    async def walk(path, name):
        listing = await nextcloud_files.read_folder(path)
        for entry in listing["elements"]:
            inner_name = f"{name}/{entry['nom']}"
            if entry["dossier"]:
                await walk(entry["chemin"], inner_name)
            else:
                found.append((entry["chemin"], inner_name, entry.get("taille") or 0))

    for path in paths:
        name = path.rstrip("/").rsplit("/", 1)[-1]
        if await nextcloud_files.is_nextcloud_folder(path):
            await walk(path, name)
        else:
            parent = path.rstrip("/").rsplit("/", 1)[0] or "/"
            entry = next(
                (item for item in (await nextcloud_files.read_folder(parent))["elements"] if item["chemin"] == path),
                None,
            )
            if not entry:
                raise ServiceError(404, f"Introuvable : {path}")
            found.append((path, name, entry.get("taille") or 0))
    return found


async def _write_nextcloud_zip(files, output_path):
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        async with nextcloud_files.client() as dav_client:
            for path, name, _ in files:
                async with dav_client.stream("GET", nextcloud_files.dav_url(path)) as response:
                    if response.status_code >= 400:
                        raise ServiceError(502, f"Nextcloud n'a pas donné « {name} ».")
                    with archive.open(name, "w", force_zip64=True) as member:
                        async for chunk in response.aiter_bytes(safe_files.CHUNK_SIZE):
                            member.write(chunk)


# ---------------------------------------------------------------- point d'entrée
async def build_zip(paths):
    paths = [nextcloud_files.clean_path(path) for path in paths if path]
    if not paths or "/" in paths:
        raise ServiceError(400, "Choisis au moins un fichier ou un dossier.")
    folder = tempfile.gettempdir()
    handle, output_path = tempfile.mkstemp(prefix="jabba-", suffix=".zip", dir=folder)
    os.close(handle)
    try:
        if files_source.files_source() == "disque":
            files = await asyncio.to_thread(_disk_files, paths)
            _check_limits(len(files), sum(size for _, _, size in files), folder)
            await asyncio.to_thread(_write_disk_zip, files, output_path)
        else:
            nextcloud_files.require_nextcloud()
            if config.DEMO:
                raise ServiceError(404, "Pas de fichiers en démo")
            files = await _nextcloud_files(paths)
            _check_limits(len(files), sum(size for _, _, size in files), folder)
            await _write_nextcloud_zip(files, output_path)
    except BaseException:
        os.unlink(output_path)
        raise
    return output_path, _zip_name(paths)
