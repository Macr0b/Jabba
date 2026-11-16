"""La porte du Wi-Fi : la page invités, seule, sur le port 80 (http://<serveur>.local).

Ce petit serveur tourne dans son propre conteneur, sans aucun pouvoir (voir compose.yaml).
Il ne connaît QUE les pages invités : la connexion admin y est impossible.
"""
import ipaddress
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from services import guests

# ---------------------------------------------------------------- la porte du Wi-Fi (port 80)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

LOCAL_NETWORKS = [ipaddress.ip_network(n) for n in
                  ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8", "100.64.0.0/10")]


@app.middleware("http")
async def wifi_guard(request: Request, call_next):
    try:
        ip = ipaddress.ip_address((request.client.host if request.client else "").split("%")[0])
        local = any(ip in n for n in LOCAL_NETWORKS)
    except ValueError:
        local = False
    if not local:
        return JSONResponse({"erreur": "Réservé au réseau local."}, status_code=403)
    request.state.via = "lan"
    if request.method not in ("GET", "HEAD") and request.headers.get("x-jabba") != "1":
        return JSONResponse({"erreur": "Requête refusée."}, status_code=403)
    reponse = await call_next(request)
    reponse.headers.setdefault("X-Frame-Options", "DENY")
    reponse.headers["X-Content-Type-Options"] = "nosniff"
    reponse.headers["Referrer-Policy"] = "no-referrer"
    return reponse


app.include_router(guests.router)


@app.get("/api/moi")
def who_am_i_on_wifi():
    import socket
    from version import VERSION
    return {"mode": "invite", "serveur": guests.read().get("serveur") or socket.gethostname(), "version": VERSION, "admin": None}


@app.get("/api/{reste:path}")
@app.post("/api/{reste:path}")
def forbidden(reste: str):
    raise HTTPException(404, "Indisponible sur le Wi-Fi : l'administration passe par Tailscale.")


app.mount("/static", StaticFiles(directory=guests.STATIC_DIR), name="static")


@app.get("/")
@app.get("/{reste:path}")
def wifi_page(reste: str = ""):
    return FileResponse(os.path.join(guests.STATIC_DIR, "index.html"), headers={"Cache-Control": "no-cache"})
