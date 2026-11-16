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
    d = guests.read()
    return {"actif": d["actif"], "code": d["code"], "url": guest_network.guest_url(d), "nom_local": f"http://{guard.hostname()}.local",
            "dossiers": d["dossiers"], "wifi": d["wifi"],
            "outils": [{**o, "wifi": bool(d["wifi"].get(o["id"]))} for o in d["outils"]]}


@router.get("/api/invites/qr.svg")
def guest_qr_code(request: Request):
    guard.require_admin(request)
    import io
    import segno
    url = guest_network.guest_url(guests.read())
    if not url:
        raise HTTPException(404, "Adresse du serveur introuvable.")
    tampon = io.BytesIO()
    segno.make(url, error="m").save(tampon, kind="svg", scale=8, border=2, dark="#111418", light="#ffffff")
    return Response(tampon.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.post("/api/invites")
async def update_guest_settings(request: Request):
    j = guard.require_admin(request)
    corps = await request.json()
    action = corps.get("action")
    if action == "actif":
        actif = bool(corps.get("actif"))
        guests.update(lambda d: d.update(actif=actif))
        if actif:
            guests.prepare_guest_folder()
        texte = f"Accès invités {'ouvert' if actif else 'fermé'}"
    elif action == "code":
        guests.new_code()
        texte = "Nouveau code invités (les invités déjà entrés devront le ressaisir)"
    elif action == "wifi":
        pid, ouvert = corps.get("outil"), bool(corps.get("wifi"))
        if pid not in tools.PORTS:
            raise HTTPException(400, "Outil inconnu.")
        if pid in tools.NEVER_ON_WIFI and ouvert:
            raise HTTPException(403, "Cet outil donne la main sur le serveur : il reste réservé à Tailscale.")
        guests.update(lambda d: d["wifi"].update({pid: ouvert}))
        if not guest_network.apply_firewall():
            raise HTTPException(500, "Le réglage est noté, mais le filtre réseau n'a pas pu être appliqué.")
        texte = f"{tools.NAMES.get(pid, (pid,))[0]} {'joignable' if ouvert else 'plus joignable'} depuis le Wi-Fi"
    elif action == "ajouter_dossier":
        virtuel, mode = nextcloud_files.clean_path(corps.get("chemin", "")), corps.get("mode", "lecture")
        reel, _ = files_source.run_disk_action(lambda: disk_browser.resolve(virtuel, guest_network.shareable_roots(), set()))
        if not reel or not os.path.isdir(reel):
            raise HTTPException(404, "Dossier introuvable.")
        if mode not in ("lecture", "depot"):
            raise HTTPException(400, "Mode inconnu.")
        nom = virtuel.rstrip("/").rsplit("/", 1)[-1]

        def f(d):
            if any(x["chemin"] == reel for x in d["dossiers"]):
                raise HTTPException(409, "Ce dossier est déjà partagé.")
            d["dossiers"].append({"id": uuid.uuid4().hex[:8], "nom": nom, "chemin": reel, "mode": mode})
        guests.update(f)
        texte = f"Dossier « {nom} » partagé avec les invités ({'dépôt autorisé' if mode == 'depot' else 'lecture seule'})"
    elif action in ("retirer_dossier", "mode_dossier"):
        did = corps.get("id")
        mode = corps.get("mode")

        def f(d):
            x = next((x for x in d["dossiers"] if x["id"] == did), None)
            if not x:
                raise HTTPException(404, "Dossier introuvable.")
            if action == "retirer_dossier":
                d["dossiers"] = [y for y in d["dossiers"] if y["id"] != did]
            elif mode in ("lecture", "depot"):
                x["mode"] = mode
            return x["nom"]
        nom = guests.update(f)
        texte = f"Dossier « {nom} » " + ("retiré des invités" if action == "retirer_dossier" else f"en {'dépôt autorisé' if mode == 'depot' else 'lecture seule'} pour les invités")
    else:
        raise HTTPException(400, "Action inconnue.")
    activity_log.record("invite", f"{texte}, par {j['identifiant']}", request.state.nom)
    return {"ok": True}


@router.get("/api/invites/parcourir")
def browse_shareable_folders(request: Request, chemin: str = "/"):
    """Les dossiers du serveur, pour choisir ceux à partager (lecture directe du disque)."""
    guard.require_admin(request)
    chemin = nextcloud_files.clean_path(chemin)
    if chemin == "/":
        guests.prepare_guest_folder()
        toutes = {**disk_browser.roots(), **guest_network.shareable_roots()}
        elements = [{"nom": n, "chemin": "/" + n} for n, c in toutes.items() if os.path.isdir(c)]
        return {"chemin": "/", "elements": elements}
    d = files_source.run_disk_action(lambda: disk_browser.list_folder(chemin, guest_network.shareable_roots(), set()))
    return {"chemin": chemin, "elements": [{"nom": e["nom"], "chemin": e["chemin"]} for e in d["elements"] if e["dossier"]]}
