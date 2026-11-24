"""La porte du Wi-Fi : la page invités, seule, sur le port 80 (http://<serveur>.local).

Ce petit serveur tourne dans son propre conteneur, sans aucun pouvoir (voir compose.yaml).
Il ne connaît QUE les pages invités : la connexion admin y est impossible.

Sommaire :
  guard_policy          : même politique de sécurité que l'appli, sans cadres : la page invités n'ouvre rien dedans
  use_disk_for_uploads  : les envois en cours de réception vont sur le disque des invités, pas en mémoire
  wifi_guard            : porte du Wi-Fi : n'ouvre qu'au réseau local, et exige l'en-tête X-Jabba pour écrire
  who_am_i_on_wifi      : ce que l'écran invité sait de lui-même (mode invité, nom du serveur, version)
  forbidden             : refuse tout le reste : l'administration ne passe que par Tailscale
  wifi_page             : la page des invités
"""

import ipaddress
import os
import tempfile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from interface_files import InterfaceFiles, index_page

from routes import guests as guest_pages
from services import guests


# ---------------------------------------------------------------- la porte du Wi-Fi (port 80)
def guard_policy():
    """Même politique de sécurité que l'appli, sans cadres : la page invités n'ouvre rien dedans."""
    return (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "media-src 'self' blob:; connect-src 'self'; worker-src 'self' blob:; frame-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )


def use_disk_for_uploads():
    """Les envois en cours de réception vont sur le disque des invités, pas en mémoire.

    Le conteneur n'a qu'un /tmp en mémoire : un gros dépôt l'aurait rempli. On le met donc
    dans un dossier caché du dossier des invités (les noms en « . » ne s'affichent jamais).
    """
    folder = os.path.join(guests.GUEST_FOLDER, ".envois-en-cours")
    try:
        os.makedirs(folder, exist_ok=True)
        for leftover in os.listdir(folder):
            os.remove(os.path.join(folder, leftover))
        tempfile.tempdir = folder
    except OSError:
        pass


use_disk_for_uploads()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

LOCAL_NETWORKS = [
    ipaddress.ip_network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8", "100.64.0.0/10")
]


@app.middleware("http")
async def wifi_guard(request: Request, call_next):
    try:
        address = ipaddress.ip_address((request.client.host if request.client else "").split("%")[0])
        is_local = any(address in network for network in LOCAL_NETWORKS)
    except ValueError:
        is_local = False
    if not is_local:
        return JSONResponse({"error": "Réservé au réseau local."}, status_code=403)
    request.state.via = "lan"
    if request.method not in ("GET", "HEAD") and request.headers.get("x-jabba") != "1":
        return JSONResponse({"error": "Requête refusée."}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", guard_policy())
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


app.include_router(guest_pages.router)


@app.get("/api/moi")
def who_am_i_on_wifi():
    import socket

    from version import VERSION

    return {
        "mode": "invite",
        "server": guests.read().get("server") or socket.gethostname(),
        "version": VERSION,
        "admin": None,
    }


@app.get("/api/{reste:path}")
@app.post("/api/{reste:path}")
def forbidden(reste: str):
    raise HTTPException(404, "Indisponible sur le Wi-Fi : l'administration passe par Tailscale.")


app.mount("/static", InterfaceFiles(directory=guests.STATIC_DIR), name="static")


@app.get("/")
@app.get("/{reste:path}")
def wifi_page(reste: str = ""):
    return index_page(guests.STATIC_DIR)
