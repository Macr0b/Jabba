"""routes.guests_admin"""

import os
import uuid
from services import guests
from services import disk_browser
from services import activity_log
from fastapi import HTTPException, Request
from fastapi.responses import Response
from security import guard
from services import files_source
from services import guest_network
from services import nextcloud_files
from services import tools
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/invites")
def guest_settings(request: Request):
    guard.require_admin(request)
    try:
        guest_network.sync_guest_tools()
    except HTTPException:
        pass
    state = guests.read()
    return {
        "actif": state["actif"],
        "code": state["code"],
        "url": guest_network.guest_url(state),
        "nom_local": f"http://{guard.hostname()}.local",
        "dossiers": state["dossiers"],
        "wifi": state["wifi"],
        "outils": [{**tool, "wifi": bool(state["wifi"].get(tool["id"]))} for tool in state["outils"]],
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


@router.post("/api/invites")
async def update_guest_settings(request: Request):
    session = guard.require_admin(request)
    body = await request.json()
    action = body.get("action")
    if action == "actif":
        active = bool(body.get("actif"))
        guests.update(lambda state: state.update(actif=active))
        if active:
            guests.prepare_guest_folder()
        text = f"Accès invités {'ouvert' if active else 'fermé'}"
    elif action == "code":
        guests.new_code()
        text = "Nouveau code invités (les invités déjà entrés devront le ressaisir)"
    elif action == "wifi":
        pid, opened = body.get("outil"), bool(body.get("wifi"))
        if pid not in tools.PORTS:
            raise HTTPException(400, "Outil inconnu.")
        if pid in tools.NEVER_ON_WIFI and opened:
            raise HTTPException(403, "Cet outil donne la main sur le serveur : il reste réservé à Tailscale.")
        guests.update(lambda state: state["wifi"].update({pid: opened}))
        if not guest_network.apply_firewall():
            raise HTTPException(500, "Le réglage est noté, mais le filtre réseau n'a pas pu être appliqué.")
        text = f"{tools.NAMES.get(pid, (pid,))[0]} {'joignable' if opened else 'plus joignable'} depuis le Wi-Fi"
    elif action == "ajouter_dossier":
        virtual_path, mode = nextcloud_files.clean_path(body.get("chemin", "")), body.get("mode", "lecture")
        real_path, _ = files_source.run_disk_action(
            lambda: disk_browser.resolve(virtual_path, guest_network.shareable_roots(), set())
        )
        if not real_path or not os.path.isdir(real_path):
            raise HTTPException(404, "Dossier introuvable.")
        if mode not in ("lecture", "depot"):
            raise HTTPException(400, "Mode inconnu.")
        name = virtual_path.rstrip("/").rsplit("/", 1)[-1]

        def f(state):
            if any(folder["chemin"] == real_path for folder in state["dossiers"]):
                raise HTTPException(409, "Ce dossier est déjà partagé.")
            state["dossiers"].append({"id": uuid.uuid4().hex[:8], "nom": name, "chemin": real_path, "mode": mode})

        guests.update(f)
        text = (
            f"Dossier « {name} » partagé avec les invités ({'dépôt autorisé' if mode == 'depot' else 'lecture seule'})"
        )
    elif action in ("retirer_dossier", "mode_dossier"):
        did = body.get("id")
        mode = body.get("mode")

        def f(state):
            folder = next((folder for folder in state["dossiers"] if folder["id"] == did), None)
            if not folder:
                raise HTTPException(404, "Dossier introuvable.")
            if action == "retirer_dossier":
                state["dossiers"] = [other for other in state["dossiers"] if other["id"] != did]
            elif mode in ("lecture", "depot"):
                folder["mode"] = mode
            return folder["nom"]

        name = guests.update(f)
        text = f"Dossier « {name} » " + (
            "retiré des invités"
            if action == "retirer_dossier"
            else f"en {'dépôt autorisé' if mode == 'depot' else 'lecture seule'} pour les invités"
        )
    else:
        raise HTTPException(400, "Action inconnue.")
    activity_log.record("invite", f"{text}, par {session['identifiant']}", request.state.nom)
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
            {"nom": name, "chemin": "/" + name} for name, disk_path in all_entries.items() if os.path.isdir(disk_path)
        ]
        return {"chemin": "/", "elements": folder_entries}
    state = files_source.run_disk_action(
        lambda: disk_browser.list_folder(chemin, guest_network.shareable_roots(), set())
    )
    return {
        "chemin": chemin,
        "elements": [
            {"nom": entries["nom"], "chemin": entries["chemin"]} for entries in state["elements"] if entries["dossier"]
        ],
    }
