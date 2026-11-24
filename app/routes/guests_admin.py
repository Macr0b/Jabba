"""API des réglages invités (côté admin) : code, dossiers partagés, outils du Wi-Fi.

Sommaire :
  GET  /api/invites             guest_settings
  GET  /api/invites/qr.svg      guest_qr_code
  POST /api/invites             update_guest_settings (un geste de GUEST_ACTIONS)
  interne : _switch, _new_code, _wifi, _mode_text, _add_folder, _change_folder
  GET  /api/invites/parcourir   browse_shareable_folders
"""

import os
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from security import guard
from services import activity_log, disk_browser, files_source, guest_network, guests, nextcloud_files, tools
from services.service_error import ServiceError

router = APIRouter()


@router.get("/api/invites")
def guest_settings(request: Request):
    guard.require_admin(request)
    try:
        guest_network.sync_guest_tools()
    except (HTTPException, ServiceError):
        pass
    state = guests.read()
    return {
        "active": state["active"],
        "code": state["code"],
        "url": guest_network.guest_url(state),
        "local_name": f"http://{guard.hostname()}.local",
        "folders": state["folders"],
        "wifi": state["wifi"],
        "tools": [{**tool, "wifi": bool(state["wifi"].get(tool["id"]))} for tool in state["tools"]],
    }


@router.get("/api/invites/qr.svg")
def guest_qr_code(request: Request):
    guard.require_admin(request)
    import io

    import segno

    url = guest_network.guest_url(guests.read())
    if not url:
        raise HTTPException(404, "Adresse du serveur introuvable.")
    buffer = io.BytesIO()
    segno.make(url, error="m").save(buffer, kind="svg", scale=8, border=2, dark="#111418", light="#ffffff")
    return Response(buffer.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- un réglage de l'accès invités
# Chaque geste : (body) -> texte du Journal.
def _switch(body):
    active = bool(body.get("active"))
    guests.update(lambda state: state.update(active=active))
    if active:
        guests.prepare_guest_folder()
    return f"Accès invités {'ouvert' if active else 'fermé'}"


def _new_code(body):
    guests.new_code()
    return "Nouveau code invités (les invités déjà entrés devront le ressaisir)"


def _wifi(body):
    pid, opened = body.get("outil"), bool(body.get("wifi"))
    if pid not in tools.PORTS:
        raise HTTPException(400, "Outil inconnu.")
    if pid in tools.NEVER_ON_WIFI and opened:
        raise HTTPException(403, "Cet outil donne la main sur le serveur : il reste réservé à Tailscale.")
    guests.update(lambda state: state["wifi"].update({pid: opened}))
    if not guest_network.apply_firewall():
        raise HTTPException(500, "Le réglage est noté, mais le filtre réseau n'a pas pu être appliqué.")
    return f"{tools.NAMES.get(pid, (pid,))[0]} {'joignable' if opened else 'plus joignable'} depuis le Wi-Fi"


def _mode_text(mode):
    return "dépôt autorisé" if mode == "depot" else "lecture seule"


def _add_folder(body):
    virtual_path, mode = nextcloud_files.clean_path(body.get("path", "")), body.get("mode", "lecture")
    real_path, _ = files_source.run_disk_action(
        lambda: disk_browser.resolve(virtual_path, guest_network.shareable_roots(), set())
    )
    if not real_path or not os.path.isdir(real_path):
        raise HTTPException(404, "Dossier introuvable.")
    if mode not in ("lecture", "depot"):
        raise HTTPException(400, "Mode inconnu.")
    name = virtual_path.rstrip("/").rsplit("/", 1)[-1]

    def add(state):
        if any(folder["path"] == real_path for folder in state["folders"]):
            raise HTTPException(409, "Ce dossier est déjà partagé.")
        state["folders"].append({"id": uuid.uuid4().hex[:8], "name": name, "path": real_path, "mode": mode})

    guests.update(add)
    return f"Dossier « {name} » partagé avec les invités ({_mode_text(mode)})"


def _change_folder(body, remove):
    folder_id, mode = body.get("id"), body.get("mode")

    def change(state):
        folder = next((folder for folder in state["folders"] if folder["id"] == folder_id), None)
        if not folder:
            raise HTTPException(404, "Dossier introuvable.")
        if remove:
            state["folders"] = [other for other in state["folders"] if other["id"] != folder_id]
        elif mode in ("lecture", "depot"):
            folder["mode"] = mode
        return folder["name"]

    name = guests.update(change)
    if remove:
        return f"Dossier « {name} » retiré des invités"
    return f"Dossier « {name} » en {_mode_text(mode)} pour les invités"


GUEST_ACTIONS = {
    "actif": _switch,
    "code": _new_code,
    "wifi": _wifi,
    "ajouter_dossier": _add_folder,
    "retirer_dossier": lambda body: _change_folder(body, remove=True),
    "mode_dossier": lambda body: _change_folder(body, remove=False),
}


@router.post("/api/invites")
async def update_guest_settings(request: Request):
    session = guard.require_admin(request)
    body = await request.json()
    action = body.get("action")
    if action not in GUEST_ACTIONS:
        raise HTTPException(400, "Action inconnue.")
    text = GUEST_ACTIONS[action](body)
    activity_log.record("invite", f"{text}, par {session['username']}", request.state.nom)
    return {"ok": True}


@router.get("/api/invites/parcourir")
def browse_shareable_folders(request: Request, chemin: str = "/"):
    """Les dossiers du serveur, pour choisir ceux à partager (lecture directe du disque)."""
    guard.require_admin(request)
    chemin = nextcloud_files.clean_path(chemin)
    if chemin == "/":
        guests.prepare_guest_folder()
        all_entries = {**disk_browser.roots(), **guest_network.shareable_roots()}
        folder_entries = [
            {"name": name, "path": "/" + name} for name, disk_path in all_entries.items() if os.path.isdir(disk_path)
        ]
        return {"path": "/", "items": folder_entries}
    state = files_source.run_disk_action(
        lambda: disk_browser.list_folder(chemin, guest_network.shareable_roots(), set())
    )
    return {
        "path": chemin,
        "items": [
            {"name": entries["name"], "path": entries["path"]} for entries in state["items"] if entries["folder"]
        ],
    }
