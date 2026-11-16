"""Écran Fichiers quand Nextcloud est désactivé : lecture directe des dossiers du serveur.

La racine est virtuelle : chaque entrée est un dossier du serveur, monté dans le
conteneur sous /srv. Rien n'est jamais lu ni écrit en dehors de ces dossiers.
"""

import hashlib
import json
import os
import subprocess
import shutil
import time
from datetime import datetime, timezone

UID, GID = 1000, 1000


def roots():
    """Nom affiché -> chemin sur le serveur. Réglable par DOSSIERS_DISQUE (« nom=chemin,… »)."""
    raw = os.environ.get(
        "DOSSIERS_DISQUE",
        "Films et séries=/srv/data/media,Téléchargements=/srv/data/downloads,Mes dossiers=/srv/data/dossiers",
    )
    result = {}
    for share in raw.split(","):
        if "=" in share:
            name, path = share.split("=", 1)
            result[name.strip()] = path.strip()
    return result


class Refusal(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def resolve(path, disks, read_only):
    """Chemin virtuel « /Films et séries/xx » -> (chemin réel, écriture autorisée)."""
    shares = [parts_list for parts_list in path.split("/") if parts_list]
    if not shares:
        return None, False
    all_entries = dict(roots())
    all_entries.update(disks)
    if shares[0] not in all_entries:
        raise Refusal(404, "Dossier introuvable")
    base = os.path.realpath(all_entries[shares[0]])
    real_path = os.path.realpath(os.path.join(base, *shares[1:]))
    if real_path != base and not real_path.startswith(base + os.sep):
        raise Refusal(400, "Chemin invalide")
    writable = shares[0] not in read_only
    return real_path, writable


def _entry(virtual_path, real_path, writable, external):
    stat = os.stat(real_path)
    folder = os.path.isdir(real_path)
    return {
        "nom": virtual_path.rsplit("/", 1)[-1],
        "chemin": virtual_path,
        "dossier": folder,
        "taille": None if folder else stat.st_size,
        "modifie": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "type": None,
        "ecriture": writable,
        "externe": external,
    }


def list_folder(path, disks, read_only):
    if path == "/":
        entries = []
        for name, real_path in list(roots().items()) + list(disks.items()):
            if name == "Mes dossiers":
                os.makedirs(real_path, exist_ok=True)
                _give_to_server_user(real_path)
            if os.path.isdir(real_path):
                entries.append(_entry("/" + name, real_path, name not in read_only, name in disks))
        return {"chemin": "/", "elements": entries, "ecriture": False, "lien_nextcloud": None, "source": "disque"}
    real_path, writable = resolve(path, disks, read_only)
    if not os.path.isdir(real_path):
        raise Refusal(404, "Dossier introuvable")
    entries = []
    for name in sorted(os.listdir(real_path), key=str.lower):
        if name.startswith("."):
            continue
        try:
            entries.append(_entry(path.rstrip("/") + "/" + name, os.path.join(real_path, name), writable, False))
        except OSError:
            continue
    entries.sort(key=lambda entry: (not entry["dossier"], entry["nom"].lower()))
    return {"chemin": path, "elements": entries, "ecriture": writable, "lien_nextcloud": None, "source": "disque"}


def _give_to_server_user(path):
    """Les fichiers créés par l'appli appartiennent à l'utilisateur du serveur, groupe du dossier parent."""
    try:
        group_id = os.stat(os.path.dirname(path)).st_gid
        os.chown(path, UID, group_id if group_id else GID)
    except OSError:
        pass


def _writable_path(path, disks, read_only):
    real_path, writable = resolve(path, disks, read_only)
    if real_path is None or not writable or len([parts_list for parts_list in path.split("/") if parts_list]) < 2:
        raise Refusal(403, "Impossible ici : dossier en lecture seule.")
    return real_path


def create_folder(path, disks, read_only):
    real_path = _writable_path(path, disks, read_only)
    if os.path.exists(real_path):
        raise Refusal(409, "Un élément porte déjà ce nom.")
    os.mkdir(real_path)
    _give_to_server_user(real_path)


def upload_path(folder, name, disks, read_only):
    real_path = _writable_path(folder.rstrip("/") + "/" + name, disks, read_only)
    return real_path


def finish_upload(real_path):
    _give_to_server_user(real_path)


def read_path(path, disks, read_only):
    real_path, _ = resolve(path, disks, read_only)
    if real_path is None or not os.path.isfile(real_path):
        raise Refusal(404, "Fichier introuvable")
    return real_path


def move(source, target_path, disks, read_only):
    source_path = _writable_path(source, disks, read_only)
    destination = _writable_path(target_path, disks, read_only)
    if not os.path.exists(source_path):
        raise Refusal(404, "Introuvable")
    if os.path.exists(destination):
        raise Refusal(409, "Un élément porte déjà ce nom.")
    shutil.move(source_path, destination)


TRASH_NAME = os.environ.get("JABBA_CORBEILLE", "/srv/data/.corbeille")
TRASH_KEEP = 30 * 86400


def delete(path, disks, read_only):
    """Pas de suppression définitive : l'élément part dans la corbeille du disque de données (30 jours)."""
    real_path = _writable_path(path, disks, read_only)
    if not os.path.exists(real_path):
        raise Refusal(404, "Introuvable")
    identifier = time.strftime("%Y%m%d-%H%M%S-") + os.urandom(3).hex()
    trash_dir = os.path.join(TRASH_NAME, identifier)
    os.makedirs(trash_dir, exist_ok=True)
    folder = os.path.isdir(real_path)
    size = None if folder else os.path.getsize(real_path)
    shutil.move(real_path, os.path.join(trash_dir, os.path.basename(real_path)))
    with open(os.path.join(trash_dir, ".jabba.json"), "w") as file:
        json.dump({"chemin": path, "t": int(time.time()), "dossier": folder, "taille": size}, file, ensure_ascii=False)


def _trash_dirs():
    try:
        names = sorted(os.listdir(TRASH_NAME), reverse=True)
    except OSError:
        return
    for identifier in names:
        trash_dir = os.path.join(TRASH_NAME, identifier)
        if not os.path.isdir(trash_dir):
            continue
        try:
            with open(os.path.join(trash_dir, ".jabba.json")) as file:
                metadata = json.load(file)
        except (OSError, ValueError):
            metadata = {}
        content = [name for name in os.listdir(trash_dir) if name != ".jabba.json"]
        if not content:
            continue
        metadata.setdefault("t", int(os.stat(trash_dir).st_mtime))
        yield identifier, trash_dir, content[0], metadata


def list_trash():
    result = []
    for identifier, trash_dir, name, metadata in _trash_dirs():
        real_path = os.path.join(trash_dir, name)
        origin = metadata.get("chemin")
        result.append(
            {
                "id": identifier,
                "nom": name,
                "origine": (origin.rsplit("/", 1)[0] or "/") if origin else None,
                "supprime": metadata["t"],
                "dossier": os.path.isdir(real_path),
                "taille": metadata.get("taille"),
                "source": "disque",
            }
        )
    return result


def _trash_dir(identifier):
    if not identifier or "/" in identifier or identifier.startswith("."):
        raise Refusal(400, "Élément invalide")
    for index, trash_dir, name, metadata in _trash_dirs():
        if index == identifier:
            return trash_dir, name, metadata
    raise Refusal(404, "Introuvable dans la corbeille")


def restore(identifier, disks, read_only):
    trash_dir, name, metadata = _trash_dir(identifier)
    origin = metadata.get("chemin")
    if not origin:
        raise Refusal(409, "On ne sait plus d'où vient cet élément.")
    parent = origin.rsplit("/", 1)[0] or "/"
    destination_path = origin
    real_path = _writable_path(destination_path, disks, read_only)
    if not os.path.isdir(os.path.dirname(real_path)):
        raise Refusal(409, f"Le dossier d'origine « {parent} » n'existe plus.")
    base, extension = os.path.splitext(name)
    index = 2
    while os.path.exists(real_path):
        destination_path = f"{parent.rstrip('/')}/{base} ({index}){extension}"
        real_path = _writable_path(destination_path, disks, read_only)
        index += 1
    shutil.move(os.path.join(trash_dir, name), real_path)
    shutil.rmtree(trash_dir, ignore_errors=True)
    return destination_path


def erase(identifier):
    trash_dir, _, _ = _trash_dir(identifier)
    shutil.rmtree(trash_dir)


def empty_trash():
    name = 0
    for _, trash_dir, _, _ in list(_trash_dirs()):
        shutil.rmtree(trash_dir, ignore_errors=True)
        name += 1
    return name


def purge_old_trash():
    """Efface ce qui est dans la corbeille depuis plus de 30 jours."""
    cutoff = time.time() - TRASH_KEEP
    name = 0
    for _, trash_dir, _, metadata in list(_trash_dirs()):
        if metadata["t"] < cutoff:
            shutil.rmtree(trash_dir, ignore_errors=True)
            name += 1
    # Les aperçus non regardés depuis 60 jours sont refaits à la demande.
    cutoff_value = time.time() - 60 * 86400
    for folder, _, files in os.walk(THUMBNAILS_DIR):
        for file in files:
            try:
                if os.stat(os.path.join(folder, file)).st_mtime < cutoff_value:
                    os.remove(os.path.join(folder, file))
            except OSError:
                pass
    return name


# ------------------------------------------------------------ recherche
def search(text, disks, read_only, names=None, cutoff=60, delay=6.0):
    """Cherche par nom dans les dossiers du serveur. `noms` limite aux racines voulues."""
    text = text.lower()
    end = time.time() + delay
    all_entries = dict(roots())
    all_entries.update(disks)
    result, full_command = [], True
    for root_name, base in all_entries.items():
        if names is not None and root_name not in names:
            continue
        base = os.path.realpath(base)
        for folder, subfolders, files in os.walk(base):
            subfolders[:] = sorted(subfolder for subfolder in subfolders if not subfolder.startswith("."))
            relative_path = os.path.relpath(folder, base)
            virtual_path = "/" + root_name + ("" if relative_path == "." else "/" + relative_path.replace(os.sep, "/"))
            for name in subfolders + sorted(files):
                if name.startswith(".") or text not in name.lower():
                    continue
                real_path = os.path.join(folder, name)
                try:
                    result.append(
                        _entry(virtual_path + "/" + name, real_path, root_name not in read_only, root_name in disks)
                    )
                except OSError:
                    continue
                if len(result) >= cutoff:
                    return result, False
            if time.time() > end:
                return result, False
    return result, full_command


# ------------------------------------------------------------ vignettes
THUMBNAILS_DIR = os.environ.get("JABBA_VIGNETTES", "/data/vignettes")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}


def thumbnail(real_path, size):
    """Petite image JPEG pour la liste des fichiers (gardée en cache)."""
    if os.path.splitext(real_path)[1].lower() not in IMAGE_EXTENSIONS:
        raise Refusal(404, "Pas d'aperçu")
    stat = os.stat(real_path)
    if stat.st_size > 80 * 1024 * 1024:
        raise Refusal(404, "Image trop lourde pour un aperçu")
    key = hashlib.sha1(f"{real_path}|{stat.st_mtime_ns}|{stat.st_size}|{size}".encode()).hexdigest()
    cache = os.path.join(THUMBNAILS_DIR, key[:2], key + ".jpg")
    if not os.path.exists(cache):
        from services import sandbox

        # Pillow tourne à part, sans aucun droit : une image piégée ne peut rien faire.
        try:
            jpeg = sandbox.thumbnail(real_path, size)
        except (RuntimeError, OSError, subprocess.SubprocessError):
            raise Refusal(404, "Pas d'aperçu")
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache + ".tmp", "wb") as file:
            file.write(jpeg)
        os.replace(cache + ".tmp", cache)
    return cache


def is_folder(path, disks, read_only):
    real_path, _ = resolve(path, disks, read_only)
    return bool(real_path) and os.path.isdir(real_path)
