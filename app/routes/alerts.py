"""routes.alerts"""
from services import activity_log
from services import notifications
from fastapi import HTTPException, Request
from services import measurements
from fastapi import APIRouter

router = APIRouter()

# ---------------------------------------------------------------- alertes (notifications)
@router.get("/api/alertes")
def api_alertes():
    e = notifications.etat()
    e.pop("endpoints")
    return e


@router.post("/api/alertes/abonnement")
async def api_alertes_abonner(request: Request):
    corps = await request.json()
    origine = request.headers.get("origin") or ""
    contact = origine if origine.startswith("https://") else "mailto:jabba@example.org"
    try:
        notifications.abonner(corps.get("abonnement"), contact, str(corps.get("appareil", "")), request.state.user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    activity_log.noter("alerte", "Alertes activées sur un appareil", request.state.nom)
    return {"ok": True}


@router.post("/api/alertes/abonnement/retirer")
async def api_alertes_desabonner(request: Request):
    notifications.desabonner(str((await request.json()).get("endpoint", "")))
    return {"ok": True}


@router.post("/api/alertes/abonnement/etat")
async def api_alertes_cet_appareil(request: Request):
    endpoint = str((await request.json()).get("endpoint", ""))
    return {"abonne": endpoint in notifications.etat()["endpoints"]}


@router.post("/api/alertes/types")
async def api_alertes_types(request: Request):
    corps = await request.json()
    try:
        notifications.regler(corps.get("type"), corps.get("actif"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/api/alertes/essai")
def api_alertes_essai(request: Request):
    n = notifications.envoyer("essai", "Alerte d'essai", f"Tout marche : {measurements.nom_serveur()} peut te prévenir.", "/#/systeme", forcer=True)
    return {"ok": True, "appareils": n}
