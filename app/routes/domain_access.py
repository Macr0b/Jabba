"""API de l'accès par le domaine (outil de l'Établi).

Sommaire :
  GET  /api/domaine          domain_status (la liste de vérification)
  POST /api/domaine          enable_domain (super admin, mot de passe confirmé : ouvre Nextcloud sur Internet)
  POST /api/domaine/retirer  disable_domain (super admin)
  POST /api/domaine/cloudflare          cloudflare_token (super admin) : garde le jeton Cloudflare (la clé
                                        OVH est oubliée), puis règle l'entrée DNS
  POST /api/domaine/cloudflare/oublier  cloudflare_forget (super admin)
  POST /api/domaine/ovh      ovh_keys (super admin) : enregistre la clé OVH puis règle l'entrée DNS
  POST /api/domaine/ovh/regler   ovh_sync (super admin) : règle l'entrée DNS maintenant (chez Cloudflare ou OVH)
  POST /api/domaine/ovh/oublier  ovh_forget (super admin) : oublie la clé et la révoque chez OVH si elle le permet
  interne : _sync_now, _ovh_forgotten
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from host import actions
from security import guard
from services import activity_log, cloudflare_dns, dns_update, domain_access, ovh_dns

router = APIRouter()


@router.get("/api/domaine")
async def domain_status(request: Request):
    guard.require_admin(request)
    return await asyncio.to_thread(domain_access.status)


@router.post("/api/domaine")
async def enable_domain(request: Request):
    guard.require_super_admin(request, strong=True)
    body = await request.json()
    try:
        settings = await asyncio.to_thread(domain_access.enable, body.get("domain"), body.get("prefix", "cloud"))
    except (ValueError, actions.ActionError) as error:
        raise HTTPException(400, str(error))
    except RuntimeError as error:
        raise HTTPException(500, str(error))
    activity_log.record(
        "securite", f"Accès par le domaine ouvert : {domain_access.address(settings)}", request.state.nom
    )
    return await asyncio.to_thread(domain_access.status, settings)


@router.post("/api/domaine/retirer")
async def disable_domain(request: Request):
    guard.require_super_admin(request)
    settings = await asyncio.to_thread(domain_access.disable)
    activity_log.record("securite", "Accès par le domaine fermé", request.state.nom)
    return await asyncio.to_thread(domain_access.status, settings)


def _sync_now():
    """Règle l'entrée DNS (Cloudflare ou OVH) pour le domaine en place ; renvoie la liste de vérification."""
    dns_update.sync_now(domain_access.read())
    return domain_access.status()


@router.post("/api/domaine/cloudflare")
async def cloudflare_token(request: Request):
    guard.require_super_admin(request)
    body = await request.json()
    zone = domain_access.read().get("domain")
    try:
        await asyncio.to_thread(cloudflare_dns.save_token, zone, body.get("token"))
    except cloudflare_dns.CloudflareError as error:
        raise HTTPException(400, str(error))
    activity_log.record("securite", "Jeton Cloudflare enregistré pour le DNS du domaine", request.state.nom)
    if ovh_dns.is_ready():  # le DNS a déménagé : l'ancienne mise à jour s'arrête
        revoked = await asyncio.to_thread(ovh_dns.forget)
        activity_log.record(
            "securite", _ovh_forgotten("Clé OVH oubliée (DNS chez Cloudflare)", revoked), request.state.nom
        )
    try:
        return await asyncio.to_thread(_sync_now)
    except dns_update.ERRORS as error:
        raise HTTPException(400, str(error))


@router.post("/api/domaine/cloudflare/oublier")
async def cloudflare_forget(request: Request):
    guard.require_super_admin(request)
    await asyncio.to_thread(cloudflare_dns.forget)
    activity_log.record("securite", "Jeton Cloudflare oublié", request.state.nom)
    return await asyncio.to_thread(domain_access.status)


@router.post("/api/domaine/ovh")
async def ovh_keys(request: Request):
    guard.require_super_admin(request)
    body = await request.json()
    zone = domain_access.read().get("domain")
    try:
        await asyncio.to_thread(
            ovh_dns.save_keys,
            zone,
            body.get("application_key"),
            body.get("application_secret"),
            body.get("consumer_key"),
        )
        activity_log.record("securite", "Clé OVH enregistrée pour le DNS du domaine", request.state.nom)
        return await asyncio.to_thread(_sync_now)
    except ovh_dns.OvhError as error:
        raise HTTPException(400, str(error))


@router.post("/api/domaine/ovh/regler")
async def ovh_sync(request: Request):
    guard.require_super_admin(request)
    try:
        return await asyncio.to_thread(_sync_now)
    except dns_update.ERRORS as error:
        raise HTTPException(400, str(error))


@router.post("/api/domaine/ovh/oublier")
async def ovh_forget(request: Request):
    guard.require_super_admin(request)
    revoked = await asyncio.to_thread(ovh_dns.forget)
    activity_log.record("securite", _ovh_forgotten("Clé OVH oubliée", revoked), request.state.nom)
    return {**await asyncio.to_thread(domain_access.status), "ovh_revoked": revoked}


def _ovh_forgotten(text, revoked):
    return text + (" et révoquée chez OVH" if revoked else " (à révoquer chez OVH : elle n'a pas ce droit)")
