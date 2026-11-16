"""Appli Jabba : point d'entrée du serveur web.

Tourne sur le serveur (réseau de l'hôte, 127.0.0.1:8090) et n'est joignable que par
Tailscale (tailscale serve). Ce fichier ne fait que brancher les morceaux :

  1. l'application et son contrôle d'accès (security/guard.py) ;
  2. les routes de l'API, une partie par fichier dans routes/ ;
  3. le démarrage (journal de version, invités, surveillance en fond : watchers.py) ;
  4. l'interface (fichiers de static/), servie en dernier.
"""
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
import watchers
from routes import (accounts, admin_session, alerts, app_update, backups, cloud, disks, files, guests_admin, player,
                    system, terminal, tools)
from security import guard
from services import activity_log, guests
from version import VERSION


# ---------------------------------------------------------------- démarrage
def note_installed_version():
    """Note au Journal chaque nouvelle version installée (une fois)."""
    version_file = os.path.join(os.path.dirname(activity_log.FICHIER), "version.txt")
    try:
        previous = open(version_file).read().strip()
    except OSError:
        previous = None
    if previous == VERSION:
        return
    activity_log.noter("appli", f"Appli Jabba {VERSION} installée" + (f" (avant : {previous})" if previous else ""))
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
        guests.preparer_etat()
        guests.modifier(lambda state: state.update(serveur=guard.nom_hote()))
    except OSError as error:
        print("invités :", error, flush=True)
    threading.Thread(target=watchers.surveiller, daemon=True, name="surveillance").start()
    yield


# ---------------------------------------------------------------- application
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.middleware("http")(guard.garde)

# Pages invités : ouvertes tant qu'on n'est pas connecté en admin.
app.include_router(guests.routeur)
for module in (admin_session, accounts, system, tools, terminal, files, disks, player, alerts, cloud, backups,
               app_update, guests_admin):
    app.include_router(module.router)


# ---------------------------------------------------------------- interface (toujours en dernier)
@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(config.STATIC, "sw.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=config.STATIC), name="static")


@app.get("/")
@app.get("/{reste:path}")
def page(reste: str = ""):
    if reste.startswith("api/"):
        raise HTTPException(404)
    return FileResponse(os.path.join(config.STATIC, "index.html"), headers={"Cache-Control": "no-cache"})
