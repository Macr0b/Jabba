"""Les pages des invités (Wi-Fi sans compte, ou Tailscale sans être admin) : état, entrée avec le code,
dossiers partagés (lister, ouvrir, déposer). Servies par l'appli et par la porte du Wi-Fi (guest_door.py).
La logique (code, cookie, réglages) est dans services/guests.py.

Sommaire :
  is_allowed(request)     : invité reconnu (cookie valable), ou arrivé par Tailscale
  require_guest(request)  : les réglages des invités, sinon 401 (code demandé)
  GET  /api/invite/etat       guest_state
  POST /api/invite/entrer     enter
  GET  /api/invite/fichiers   list_files
  GET  /api/invite/fichier    get_file
  POST /api/invite/envoyer    upload
  interne : _shared_folder, _resolve
"""

import hmac
import os
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import UploadFile

from services import activity_log, guests, safe_files


# ---------------------------------------------------------------- fichiers
def _shared_folder(state, did):
    for folder in state["folders"]:
        if folder["id"] == did:
            return folder
    raise HTTPException(404, "Dossier introuvable")


def _resolve(base, path):
    base = os.path.realpath(base)
    shares = [parts_list for parts_list in (path or "").split("/") if parts_list not in ("", ".")]
    if any(parts_list == ".." or parts_list.startswith(".") for parts_list in shares):
        raise HTTPException(400, "Chemin invalide")
    real_path = os.path.realpath(os.path.join(base, *shares))
    if real_path != base and not real_path.startswith(base + os.sep):
        raise HTTPException(400, "Chemin invalide")
    return real_path


def is_allowed(request):
    if getattr(request.state, "via", "") == "tailscale":
        return True
    state = guests.read()
    return state["active"] and guests.cookie_is_valid(state, request.cookies.get(guests.COOKIE))


def require_guest(request):
    if not is_allowed(request):
        raise HTTPException(401, {"guest": True, "message": "Code d'accès demandé."})
    return guests.read()


router = APIRouter()


@router.get("/api/invite/etat")
def guest_state(request: Request):
    state = guests.read()
    on_lan = getattr(request.state, "via", "") != "tailscale"
    allowed = is_allowed(request)
    host_runner = (request.headers.get("host") or "").split(":")[0]
    tools_list = []
    if allowed:
        for tool in state["tools"]:
            if tool.get("active") and state["wifi"].get(tool["id"]) and (on_lan or tool.get("tailscale_link")):
                tools_list.append(
                    {
                        "id": tool["id"],
                        "name": tool["name"],
                        "role": tool.get("role", ""),
                        "link": f"http://{host_runner}:{tool['port']}" if on_lan else tool.get("tailscale_link"),
                    }
                )
    return {
        "active": state["active"] or not on_lan,
        "between": allowed,
        "lan": on_lan,
        "folders": [{"id": folder["id"], "name": folder["name"], "mode": folder["mode"]} for folder in state["folders"]]
        if allowed
        else [],
        "tools": tools_list,
    }


@router.post("/api/invite/entrer")
async def enter(request: Request):
    state = guests.read()
    address = request.client.host if request.client else "?"
    if not state["active"]:
        raise HTTPException(403, "L'accès invités est fermé.")
    if guests.is_blocked(address):
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    code = str((await request.json()).get("code", "")).strip()
    if not hmac.compare_digest(code, state["code"]):
        guests.record_failure(address)
        raise HTTPException(403, "Code incorrect.")
    current_state = JSONResponse({"ok": True})
    current_state.set_cookie(
        guests.COOKIE, guests.guest_cookie(state), max_age=guests.COOKIE_LIFETIME, httponly=True, samesite="strict"
    )
    activity_log.record("invite", f"Un invité est entré ({address})")
    return current_state


@router.get("/api/invite/fichiers")
def list_files(request: Request, d: str, chemin: str = "/"):
    current_state = require_guest(request)
    folder = _shared_folder(current_state, d)
    real_path = _resolve(folder["path"], chemin)
    if not os.path.isdir(real_path):
        raise HTTPException(404, "Dossier introuvable")
    entries = []
    for name in sorted(os.listdir(real_path), key=str.lower):
        if name.startswith("."):
            continue
        item_path = os.path.join(real_path, name)
        try:
            stat = os.stat(item_path)
        except OSError:
            continue
        relative_path = (chemin.rstrip("/") + "/" + name) if chemin != "/" else "/" + name
        entries.append(
            {
                "name": name,
                "path": relative_path,
                "folder": os.path.isdir(item_path),
                "size": None if os.path.isdir(item_path) else stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
        )
    entries.sort(key=lambda entry: (not entry["folder"], entry["name"].lower()))
    return {
        "folder": {"id": folder["id"], "name": folder["name"], "mode": folder["mode"]},
        "path": chemin,
        "items": entries,
    }


@router.get("/api/invite/fichier")
def get_file(request: Request, d: str, chemin: str, voir: int = 0):
    current_state = require_guest(request)
    real_path = _resolve(_shared_folder(current_state, d)["path"], chemin)
    if not os.path.isfile(real_path):
        raise HTTPException(404, "Fichier introuvable")
    name = os.path.basename(real_path)
    import mimetypes

    media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if voir and media_type.startswith(("image/", "video/", "audio/")) and media_type != "image/svg+xml":
        return FileResponse(
            real_path, media_type=media_type, headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"}
        )
    return FileResponse(real_path, filename=name)


@router.post("/api/invite/envoyer")
async def upload(request: Request, d: str, chemin: str = "/"):
    current_state = require_guest(request)
    folder = _shared_folder(current_state, d)
    if folder["mode"] != "depot":
        raise HTTPException(403, "Ce dossier est en lecture seule.")
    target = _resolve(folder["path"], chemin)
    if not os.path.isdir(target):
        raise HTTPException(404, "Dossier introuvable")
    # Taille annoncée trop grande, ou plus assez de place : refus avant même de recevoir le fichier.
    announced = int(request.headers.get("content-length") or 0)
    if announced > guests.MAX_FILE_BYTES + 1024 * 1024:
        raise HTTPException(413, f"Fichier trop gros : {guests.MAX_FILE_BYTES // 1024**3} Go au plus.")
    if safe_files.free_space(target) - announced < guests.KEEP_FREE_BYTES:
        raise HTTPException(507, "Plus assez de place sur le serveur pour ce dépôt.")
    form = await request.form()
    uploaded = []
    for file in form.getlist("fichiers"):
        if not isinstance(file, UploadFile) or not file.filename:
            continue
        name = os.path.basename(file.filename.replace("\\", "/")).lstrip(".") or "fichier"
        base, extension = os.path.splitext(name)
        real_path, counter = os.path.join(target, name), 2
        while True:  # jamais d'écrasement : « photo (2).jpg »
            try:
                output = safe_files.open_new_file(real_path)
                break
            except FileExistsError:
                real_path, counter = os.path.join(target, f"{base} ({counter}){extension}"), counter + 1
        try:
            with output:
                safe_files.copy_with_limit(file.file, output, target, guests.MAX_FILE_BYTES, guests.KEEP_FREE_BYTES)
        except safe_files.TooLarge:
            os.remove(real_path)
            raise HTTPException(413, f"Fichier trop gros : {guests.MAX_FILE_BYTES // 1024**3} Go au plus.")
        except safe_files.DiskFull:
            os.remove(real_path)
            raise HTTPException(507, "Plus assez de place sur le serveur pour ce dépôt.")
        try:
            os.chown(real_path, 1000, os.stat(target).st_gid or 1000)
        except OSError:
            pass
        uploaded.append(os.path.basename(real_path))
    if uploaded:
        activity_log.record(
            "invite",
            f"Invité : {len(uploaded)} fichier{'s' if len(uploaded) > 1 else ''} déposé{'s' if len(uploaded) > 1 else ''} dans {folder['name']}{chemin if chemin != '/' else ''}",
        )
    return {"sent": uploaded}
