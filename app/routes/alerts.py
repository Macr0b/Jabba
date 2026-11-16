"""API des alertes (notifications sur l'iPhone et le Mac).

Sommaire :
  GET  /api/alertes                     alert_settings
  POST /api/alertes/abonnement          subscribe
  POST /api/alertes/abonnement/retirer  unsubscribe
  POST /api/alertes/abonnement/etat     this_device_state
  POST /api/alertes/types               set_alert_types
  POST /api/alertes/essai               send_test_alert
"""

from fastapi import APIRouter, HTTPException, Request

from services import activity_log, measurements, notifications

router = APIRouter()


# ---------------------------------------------------------------- alertes (notifications)
@router.get("/api/alertes")
def alert_settings():
    current_settings = notifications.settings()
    current_settings.pop("endpoints")
    return current_settings


@router.post("/api/alertes/abonnement")
async def subscribe(request: Request):
    body = await request.json()
    origin = request.headers.get("origin") or ""
    contact = origin if origin.startswith("https://") else "mailto:jabba@example.org"
    try:
        notifications.subscribe(body.get("abonnement"), contact, str(body.get("appareil", "")), request.state.user)
    except ValueError as current_settings:
        raise HTTPException(400, str(current_settings))
    activity_log.record("alerte", "Alertes activées sur un appareil", request.state.nom)
    return {"ok": True}


@router.post("/api/alertes/abonnement/retirer")
async def unsubscribe(request: Request):
    notifications.unsubscribe(str((await request.json()).get("endpoint", "")))
    return {"ok": True}


@router.post("/api/alertes/abonnement/etat")
async def this_device_state(request: Request):
    endpoint = str((await request.json()).get("endpoint", ""))
    return {"abonne": endpoint in notifications.settings()["endpoints"]}


@router.post("/api/alertes/types")
async def set_alert_types(request: Request):
    body = await request.json()
    try:
        notifications.set_types(body.get("type"), body.get("actif"))
    except ValueError as current_settings:
        raise HTTPException(400, str(current_settings))
    return {"ok": True}


@router.post("/api/alertes/essai")
def send_test_alert(request: Request):
    sent_count = notifications.send(
        "essai",
        "Alerte d'essai",
        f"Tout marche : {measurements.server_name()} peut te prévenir.",
        "/#/systeme",
        force=True,
    )
    return {"ok": True, "appareils": sent_count}
