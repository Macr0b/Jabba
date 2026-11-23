"""Appli Jabba : point d'entrée du serveur web.

Tourne sur le serveur (réseau de l'hôte, 127.0.0.1:8090) et n'est joignable que par
Tailscale (tailscale serve). Ce fichier ne fait que brancher les morceaux :

  1. l'application et son contrôle d'accès (security/guard.py) ;
  2. les routes de l'API, une partie par fichier dans routes/ ;
  3. le démarrage (journal de version, invités, surveillance en fond : watchers.py) ;
  4. l'interface (fichiers de static/), servie en dernier.

Sommaire :
  note_installed_version  : note au Journal chaque nouvelle version installée (une fois)
  lifespan                : au démarrage : version, état des invités, puis la surveillance en fond
  service_error           : change une ServiceError des services en réponse {"detail": …} avec son code
  service_worker          : le service worker, jamais gardé en cache (les mises à jour arrivent tout de suite)
  page                    : la page de l'appli pour toute adresse hors API
"""

import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from interface_files import InterfaceFiles, index_page

import config
import watchers
from routes import (
    accounts,
    ad_blocker,
    admin_session,
    alerts,
    antivirus,
    app_update,
    backups,
    cloud,
    disk_health,
    disks,
    domain_access,
    files,
    files_batch,
    files_viewer,
    guests_admin,
    heartbeat,
    my_codes,
    optional_tools,
    player,
    public_mode,
    second_factor,
    server_move,
    shares,
    system,
    tool_addresses,
    tools,
    trash,
    workshop,
)
from routes import guests as guest_pages
from security import guard
from services import activity_log, guests
from services.service_error import ServiceError
from version import VERSION


# ---------------------------------------------------------------- démarrage
def note_installed_version():
    """Note au Journal chaque nouvelle version installée (une fois)."""
    version_file = os.path.join(os.path.dirname(activity_log.LOG_FILE), "version.txt")
    try:
        previous = open(version_file).read().strip()
    except OSError:
        previous = None
    if previous == VERSION:
        return
    activity_log.record("appli", f"Appli Jabba {VERSION} installée" + (f" (avant : {previous})" if previous else ""))
    try:
        os.makedirs(os.path.dirname(version_file), exist_ok=True)
        open(version_file, "w").write(VERSION)
    except OSError:
        pass


@asynccontextmanager
async def lifespan(_app):
    """Au démarrage : version, état des invités, puis la surveillance en fond."""
    note_installed_version()
    try:
        guests.prepare_state()
        guests.update(lambda state: state.update(serveur=guard.hostname()))
    except OSError as error:
        print("invités :", error, flush=True)
    threading.Thread(target=watchers.watch_forever, daemon=True, name="surveillance").start()
    yield


# ---------------------------------------------------------------- application
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.middleware("http")(guard.guard)


@app.exception_handler(ServiceError)
async def service_error(_request, error):
    """Change une ServiceError des services en réponse {"detail": …} avec son code."""
    return JSONResponse({"detail": error.message}, status_code=error.status)


# Pages invités : ouvertes tant qu'on n'est pas connecté en admin.
app.include_router(guest_pages.router)
for module in (
    antivirus,
    domain_access,
    tool_addresses,
    my_codes,
    admin_session,
    accounts,
    system,
    tools,
    files,
    files_batch,
    files_viewer,
    optional_tools,
    trash,
    workshop,
    disks,
    disk_health,
    player,
    alerts,
    cloud,
    backups,
    app_update,
    guests_admin,
    shares,
    second_factor,
    public_mode,
    ad_blocker,
    server_move,
    heartbeat,
):
    app.include_router(module.router)


# ---------------------------------------------------------------- interface (toujours en dernier)
@app.get("/sw.js")
def service_worker():
    return FileResponse(
        os.path.join(config.STATIC_DIR, "sw.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


app.mount("/static", InterfaceFiles(directory=config.STATIC_DIR), name="static")


@app.get("/")
@app.get("/{reste:path}")
def page(reste: str = ""):
    if reste.startswith("api/"):
        raise HTTPException(404)
    return index_page(config.STATIC_DIR)
