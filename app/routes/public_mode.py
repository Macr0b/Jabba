"""API de l'option « Serveur ouvert au public », et des demandes de fichiers par lien.

Sommaire :
  GET  /api/public/etat           public_state (ouvert ou non : léger, pour l'écran Fichiers)
  GET  /api/public                public_overview (réglages, liste « avant d'ouvrir », comptes inactifs)
  POST /api/public/reglages       public_settings
  POST /api/public/ouvrir         open_or_close (super admin, mot de passe récent)
  POST /api/public/mentions       add_privacy_notice (ajoute les mentions RGPD à la charte d'inscription)
  POST /api/demandes-fichiers     file_request (lien « déposer seulement » sur un dossier Nextcloud)
  interne : _visible
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from host import commands
from routes.cloud import run_cloud_action
from security import guard, second_factor
from services import activity_log, backups, files_source, nextcloud_accounts, nextcloud_shares, public_mode

router = APIRouter()
EDITABLE = {"organisation": 80, "contact": 120}


def _visible(settings):
    return {key: value for key, value in settings.items() if key not in ("before", "last_inactive_alert")}


@router.get("/api/public/etat")
def public_state(request: Request):
    guard.require_admin(request)
    return {"active": public_mode.read()["active"]}


@router.get("/api/public")
async def public_overview(request: Request):
    session = guard.require_admin(request)
    settings = public_mode.read()
    cloud = await asyncio.to_thread(nextcloud_accounts.overview, commands.occ)
    inactive = []
    if settings["active"] and cloud.get("present"):
        try:
            accounts = await asyncio.to_thread(nextcloud_accounts.accounts, commands.occ)
            inactive = public_mode.inactive_accounts(accounts, int(settings["inactive_months"]))
        except nextcloud_accounts.CloudError:
            inactive = []
    return {
        "settings": _visible(settings),
        "ready": public_mode.readiness(
            second_factor.summary(session["username"])["active"],
            bool(backups.read().get("active")),
            settings,
            bool(cloud.get("ready")),
        ),
        "mentions": public_mode.privacy_notice(settings),
        "inactive": [{"id": a["id"], "name": a["name"], "inactive_since": a["inactive_since"]} for a in inactive],
        "nextcloud": bool(cloud.get("present")),
    }


@router.post("/api/public/reglages")
async def public_settings(request: Request):
    guard.require_super_admin(request)
    body = await request.json()
    settings = public_mode.read()
    for key, limit in EDITABLE.items():
        if key in body:
            settings[key] = " ".join(str(body[key] or "").split())[:limit]
    if "inactive_months" in body:
        settings["inactive_months"] = max(3, min(int(body["inactive_months"]), 60))
    if "expiry_days" in body:
        settings["expiry_days"] = max(1, min(int(body["expiry_days"]), 365))
        if settings["active"]:
            settings = public_mode.apply_link_policy(commands.occ, True, settings)
    public_mode.write(settings)
    return _visible(settings)


@router.post("/api/public/ouvrir")
async def open_or_close(request: Request):
    session = guard.require_super_admin(request, strong=True)
    wanted = bool((await request.json()).get("active"))
    settings = public_mode.read()
    if wanted and not second_factor.summary(session["username"])["active"]:
        raise HTTPException(400, "Active d'abord la double vérification sur ton compte (Système).")
    settings = await asyncio.to_thread(public_mode.apply_link_policy, commands.occ, wanted, settings)
    settings["active"] = wanted
    public_mode.write(settings)
    activity_log.record(
        "securite", "Mode « serveur ouvert au public » " + ("activé" if wanted else "désactivé"), request.state.nom
    )
    return _visible(settings)


@router.post("/api/public/mentions")
async def add_privacy_notice(request: Request):
    guard.require_super_admin(request)
    notice = public_mode.privacy_notice(public_mode.read())
    current = run_cloud_action(nextcloud_accounts.charter)
    marker = "Données personnelles :"
    kept = current.split(marker, 1)[0].strip()
    run_cloud_action(nextcloud_accounts.set_charter, (kept + " " + notice).strip())
    activity_log.record("nuage", "Mentions RGPD ajoutées à la charte d'inscription", request.state.nom)
    return {"ok": True}


@router.post("/api/demandes-fichiers")
async def file_request(request: Request):
    guard.require_admin(request)
    if not public_mode.read()["active"]:
        raise HTTPException(400, "Les demandes de fichiers servent quand le serveur est ouvert au public (Système).")
    if files_source.files_source() != "nextcloud":
        raise HTTPException(400, "Les demandes de fichiers passent par Nextcloud.")
    body = await request.json()
    path = "/" + "/".join(part for part in str(body.get("path", "")).split("/") if part and part not in (".", ".."))
    if path == "/":
        raise HTTPException(400, "Choisis un dossier.")
    try:
        result = await asyncio.to_thread(
            public_mode.create_file_request,
            nextcloud_shares.nextcloud_ocs,
            path,
            body.get("days", 7),
            body.get("password") or "",
            body.get("title") or "",
        )
    except nextcloud_accounts.CloudError as error:
        raise HTTPException(400, str(error))
    activity_log.record(
        "fichier", f"Demande de fichiers créée sur {path} (jusqu'au {result['expire']})", request.state.nom
    )
    return result
