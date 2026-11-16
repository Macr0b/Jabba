"""Accès invités : ce que voient les personnes connectées au Wi-Fi, sans compte.

Deux portes vers les mêmes pages invités :
- le Wi-Fi : un petit serveur séparé (port 80, http://<serveur>.local), qui
  ne connaît QUE les pages invités. La connexion admin y est impossible. Il tourne
  dans son propre conteneur, sans aucun pouvoir : compte du serveur (pas root),
  aucune capacité Linux, système de fichiers en lecture seule. Il ne voit que
  /srv (les dossiers partagés) et son petit dossier d'état (donnees/porte-wifi/),
  jamais le reste des données de l'appli (comptes, clés, sessions…).
- Tailscale : l'appli, tant qu'on n'est pas connecté en admin.

Sur le Wi-Fi, il faut le code à 4 chiffres (ou le QR code qui le contient).
L'appareil est ensuite reconnu 7 jours, sauf si l'admin change le code.
Les invités ne voient que les dossiers et les outils choisis par l'admin.
"""
import hashlib
import hmac
import json
import os
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import UploadFile

from services import activity_log
import config

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
# État partagé entre l'appli (qui l'écrit) et la porte du Wi-Fi (qui le lit) : un dossier à part.
STATE_FILE = os.environ.get("JABBA_INVITES_ETAT", os.path.join(DATA_DIR, "porte-wifi", "etat.json"))
OLD_STATE_FILE = os.path.join(DATA_DIR, "invites.json")    # avant la 0.11
GUEST_FOLDER = os.environ.get("JABBA_DOSSIER_INVITES", "/srv/data/invites")
COOKIE = "jabba_invite"
COOKIE_LIFETIME = 7 * 86400
STATIC_DIR = config.STATIC_DIR
_lock = threading.RLock()
_failures = {}  # ip -> [horodatages]


# ---------------------------------------------------------------- réglages
def read():
    try:
        with open(STATE_FILE) as file:
            state = json.load(file)
    except (OSError, ValueError):
        state = {}
    state.setdefault("actif", False)
    state.setdefault("code", "%04d" % secrets.randbelow(10000))
    state.setdefault("generation", 1)
    state.setdefault("secret", secrets.token_hex(32))
    state.setdefault("dossiers", [{"id": "invites", "nom": "Invités", "chemin": GUEST_FOLDER, "mode": "depot"}])
    state.setdefault("wifi", {"jellyfin": True})   # outils joignables depuis le Wi-Fi
    state.setdefault("outils", [])                 # copie tenue à jour par l'appli : {id, nom, role, port, actif}
    state.setdefault("adresse", None)              # adresse IP du serveur sur le Wi-Fi
    return state


def write(state):
    with _lock:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        temporary_file = STATE_FILE + ".tmp"
        with open(temporary_file, "w") as file:
            json.dump(state, file, ensure_ascii=False)
        os.chmod(temporary_file, 0o600)
        _give_to_guest_account(temporary_file)
        os.replace(temporary_file, STATE_FILE)


GUEST_UID = int(os.environ.get("JABBA_UID", "1000"))
GUEST_GID = int(os.environ.get("JABBA_GID", "1000"))


def _give_to_guest_account(path):
    """L'appli (root) écrit l'état ; la porte du Wi-Fi le lit avec le compte du serveur."""
    if os.geteuid() == 0:
        try:
            os.chown(path, GUEST_UID, GUEST_GID)
        except OSError:
            pass


def prepare_state():
    """Au démarrage de l'appli : dossier d'état des invités (et reprise de l'ancien emplacement)."""
    folder = os.path.dirname(STATE_FILE)
    os.makedirs(folder, exist_ok=True)
    if os.path.exists(OLD_STATE_FILE) and not os.path.exists(STATE_FILE):
        os.replace(OLD_STATE_FILE, STATE_FILE)
    if not os.path.exists(STATE_FILE):
        write(read())          # code et secret créés une fois pour toutes, ici
    if os.geteuid() == 0:
        os.chmod(folder, 0o700)
        _give_to_guest_account(folder)
        _give_to_guest_account(STATE_FILE)


def update(function):
    with _lock:
        state = read()
        r = function(state)
        write(state)
        return r


def new_code():
    def f(state):
        state["code"] = "%04d" % secrets.randbelow(10000)
        state["generation"] += 1   # les invités déjà reconnus doivent ressaisir le code
        return state["code"]
    return update(f)


def prepare_guest_folder():
    try:
        os.makedirs(GUEST_FOLDER, exist_ok=True)
        stat = os.stat(os.path.dirname(GUEST_FOLDER))
        os.chown(GUEST_FOLDER, 1000, stat.st_gid or 1000)
    except OSError:
        pass


# ---------------------------------------------------------------- cookie invité
def _sign(state, content):
    return hmac.new(bytes.fromhex(state["secret"]), content.encode(), hashlib.sha256).hexdigest()[:32]


def guest_cookie(state):
    expiry = int(time.time()) + COOKIE_LIFETIME
    content = f"{state['generation']}.{expiry}"
    return f"{content}.{_sign(state, content)}"


def cookie_is_valid(state, value):
    try:
        generation, expiry, signature = (value or "").split(".")
    except ValueError:
        return False
    return (hmac.compare_digest(signature, _sign(state, f"{generation}.{expiry}")) and int(generation) == state["generation"]
            and int(expiry) > time.time())


def is_blocked(address):
    now = time.time()
    _failures[address] = [attempts for attempts in _failures.get(address, []) if attempts > now - 600]
    return len(_failures[address]) >= 8


# ---------------------------------------------------------------- fichiers
def _shared_folder(state, did):
    for folder in state["dossiers"]:
        if folder["id"] == did:
            return folder
    raise HTTPException(404, "Dossier introuvable")


def _resolve(base, path):
    base = os.path.realpath(base)
    shares = [parts_list for parts_list in (path or "").split("/") if parts_list not in ("", ".")]
    if any(parts_list == ".." or parts_list.startswith(".") for parts_list in shares):
        raise HTTPException(400, "Chemin invalide")
    real_path = os.path.realpath(os.path.join(base, *shares))
    if real_path != base and not real_path.startswith(base + os.sep):
        raise HTTPException(400, "Chemin invalide")
    return real_path


def is_allowed(request):
    if getattr(request.state, "via", "") == "tailscale":
        return True
    state = read()
    return state["actif"] and cookie_is_valid(state, request.cookies.get(COOKIE))


def require_guest(request):
    if not is_allowed(request):
        raise HTTPException(401, {"invite": True, "message": "Code d'accès demandé."})
    return read()


router = APIRouter()


@router.get("/api/invite/etat")
def guest_state(request: Request):
    state = read()
    on_lan = getattr(request.state, "via", "") != "tailscale"
    allowed = is_allowed(request)
    host_runner = (request.headers.get("host") or "").split(":")[0]
    tools_list = []
    if allowed:
        for tool in state["outils"]:
            if tool.get("actif") and state["wifi"].get(tool["id"]) and (on_lan or tool.get("lien_tailscale")):
                tools_list.append({"id": tool["id"], "nom": tool["nom"], "role": tool.get("role", ""),
                               "lien": f"http://{host_runner}:{tool['port']}" if on_lan else tool.get("lien_tailscale")})
    return {"actif": state["actif"] or not on_lan, "entre": allowed, "lan": on_lan,
            "dossiers": [{"id": folder["id"], "nom": folder["nom"], "mode": folder["mode"]} for folder in state["dossiers"]] if allowed else [],
            "outils": tools_list}


@router.post("/api/invite/entrer")
async def enter(request: Request):
    state = read()
    address = request.client.host if request.client else "?"
    if not state["actif"]:
        raise HTTPException(403, "L'accès invités est fermé.")
    if is_blocked(address):
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    code = str((await request.json()).get("code", "")).strip()
    if not hmac.compare_digest(code, state["code"]):
        _failures.setdefault(address, []).append(time.time())
        raise HTTPException(403, "Code incorrect.")
    r = JSONResponse({"ok": True})
    r.set_cookie(COOKIE, guest_cookie(state), max_age=COOKIE_LIFETIME, httponly=True, samesite="strict")
    activity_log.record("invite", f"Un invité est entré ({address})")
    return r


@router.get("/api/invite/fichiers")
def list_files(request: Request, d: str, chemin: str = "/"):
    r = require_guest(request)
    folder = _shared_folder(r, d)
    real_path = _resolve(folder["chemin"], chemin)
    if not os.path.isdir(real_path):
        raise HTTPException(404, "Dossier introuvable")
    entries = []
    for name in sorted(os.listdir(real_path), key=str.lower):
        if name.startswith("."):
            continue
        item_path = os.path.join(real_path, name)
        try:
            stat = os.stat(item_path)
        except OSError:
            continue
        relative_path = (chemin.rstrip("/") + "/" + name) if chemin != "/" else "/" + name
        entries.append({"nom": name, "chemin": relative_path, "dossier": os.path.isdir(item_path),
                         "taille": None if os.path.isdir(item_path) else stat.st_size,
                         "modifie": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
    entries.sort(key=lambda entry: (not entry["dossier"], entry["nom"].lower()))
    return {"dossier": {"id": folder["id"], "nom": folder["nom"], "mode": folder["mode"]}, "chemin": chemin, "elements": entries}


@router.get("/api/invite/fichier")
def get_file(request: Request, d: str, chemin: str, voir: int = 0):
    r = require_guest(request)
    real_path = _resolve(_shared_folder(r, d)["chemin"], chemin)
    if not os.path.isfile(real_path):
        raise HTTPException(404, "Fichier introuvable")
    name = os.path.basename(real_path)
    import mimetypes
    media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if voir and media_type.startswith(("image/", "video/", "audio/")) and media_type != "image/svg+xml":
        return FileResponse(real_path, media_type=media_type, headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"})
    return FileResponse(real_path, filename=name)


@router.post("/api/invite/envoyer")
async def upload(request: Request, d: str, chemin: str = "/"):
    r = require_guest(request)
    folder = _shared_folder(r, d)
    if folder["mode"] != "depot":
        raise HTTPException(403, "Ce dossier est en lecture seule.")
    target = _resolve(folder["chemin"], chemin)
    if not os.path.isdir(target):
        raise HTTPException(404, "Dossier introuvable")
    form = await request.form()
    uploaded = []
    for file in form.getlist("fichiers"):
        if not isinstance(file, UploadFile) or not file.filename:
            continue
        name = os.path.basename(file.filename.replace("\\", "/")).lstrip(".") or "fichier"
        base, extension = os.path.splitext(name)
        real_path, counter = os.path.join(target, name), 2
        while os.path.exists(real_path):   # jamais d'écrasement : « photo (2).jpg »
            real_path, counter = os.path.join(target, f"{base} ({counter}){extension}"), counter + 1
        with open(real_path, "wb") as output:
            shutil.copyfileobj(file.file, output, 1024 * 1024)
        try:
            os.chown(real_path, 1000, os.stat(target).st_gid or 1000)
        except OSError:
            pass
        uploaded.append(os.path.basename(real_path))
    if uploaded:
        activity_log.record("invite", f"Invité : {len(uploaded)} fichier{'s' if len(uploaded) > 1 else ''} déposé{'s' if len(uploaded) > 1 else ''} dans {folder['nom']}{chemin if chemin != '/' else ''}")
    return {"envoyes": uploaded}
