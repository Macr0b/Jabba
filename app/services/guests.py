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
        with open(STATE_FILE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("actif", False)
    d.setdefault("code", "%04d" % secrets.randbelow(10000))
    d.setdefault("generation", 1)
    d.setdefault("secret", secrets.token_hex(32))
    d.setdefault("dossiers", [{"id": "invites", "nom": "Invités", "chemin": GUEST_FOLDER, "mode": "depot"}])
    d.setdefault("wifi", {"jellyfin": True})   # outils joignables depuis le Wi-Fi
    d.setdefault("outils", [])                 # copie tenue à jour par l'appli : {id, nom, role, port, actif}
    d.setdefault("adresse", None)              # adresse IP du serveur sur le Wi-Fi
    return d


def write(d):
    with _lock:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        _give_to_guest_account(tmp)
        os.replace(tmp, STATE_FILE)


GUEST_UID = int(os.environ.get("JABBA_UID", "1000"))
GUEST_GID = int(os.environ.get("JABBA_GID", "1000"))


def _give_to_guest_account(chemin):
    """L'appli (root) écrit l'état ; la porte du Wi-Fi le lit avec le compte du serveur."""
    if os.geteuid() == 0:
        try:
            os.chown(chemin, GUEST_UID, GUEST_GID)
        except OSError:
            pass


def prepare_state():
    """Au démarrage de l'appli : dossier d'état des invités (et reprise de l'ancien emplacement)."""
    dossier = os.path.dirname(STATE_FILE)
    os.makedirs(dossier, exist_ok=True)
    if os.path.exists(OLD_STATE_FILE) and not os.path.exists(STATE_FILE):
        os.replace(OLD_STATE_FILE, STATE_FILE)
    if not os.path.exists(STATE_FILE):
        write(read())          # code et secret créés une fois pour toutes, ici
    if os.geteuid() == 0:
        os.chmod(dossier, 0o700)
        _give_to_guest_account(dossier)
        _give_to_guest_account(STATE_FILE)


def update(fonction):
    with _lock:
        d = read()
        r = fonction(d)
        write(d)
        return r


def new_code():
    def f(d):
        d["code"] = "%04d" % secrets.randbelow(10000)
        d["generation"] += 1   # les invités déjà reconnus doivent ressaisir le code
        return d["code"]
    return update(f)


def prepare_guest_folder():
    try:
        os.makedirs(GUEST_FOLDER, exist_ok=True)
        st = os.stat(os.path.dirname(GUEST_FOLDER))
        os.chown(GUEST_FOLDER, 1000, st.st_gid or 1000)
    except OSError:
        pass


# ---------------------------------------------------------------- cookie invité
def _sign(d, contenu):
    return hmac.new(bytes.fromhex(d["secret"]), contenu.encode(), hashlib.sha256).hexdigest()[:32]


def guest_cookie(d):
    exp = int(time.time()) + COOKIE_LIFETIME
    contenu = f"{d['generation']}.{exp}"
    return f"{contenu}.{_sign(d, contenu)}"


def cookie_is_valid(d, valeur):
    try:
        gen, exp, sig = (valeur or "").split(".")
    except ValueError:
        return False
    return (hmac.compare_digest(sig, _sign(d, f"{gen}.{exp}")) and int(gen) == d["generation"]
            and int(exp) > time.time())


def is_blocked(ip):
    maintenant = time.time()
    _failures[ip] = [t for t in _failures.get(ip, []) if t > maintenant - 600]
    return len(_failures[ip]) >= 8


# ---------------------------------------------------------------- fichiers
def _shared_folder(d, did):
    for x in d["dossiers"]:
        if x["id"] == did:
            return x
    raise HTTPException(404, "Dossier introuvable")


def _resolve(base, chemin):
    base = os.path.realpath(base)
    morceaux = [m for m in (chemin or "").split("/") if m not in ("", ".")]
    if any(m == ".." or m.startswith(".") for m in morceaux):
        raise HTTPException(400, "Chemin invalide")
    reel = os.path.realpath(os.path.join(base, *morceaux))
    if reel != base and not reel.startswith(base + os.sep):
        raise HTTPException(400, "Chemin invalide")
    return reel


def is_allowed(request):
    if getattr(request.state, "via", "") == "tailscale":
        return True
    d = read()
    return d["actif"] and cookie_is_valid(d, request.cookies.get(COOKIE))


def require_guest(request):
    if not is_allowed(request):
        raise HTTPException(401, {"invite": True, "message": "Code d'accès demandé."})
    return read()


router = APIRouter()


@router.get("/api/invite/etat")
def guest_state(request: Request):
    d = read()
    lan = getattr(request.state, "via", "") != "tailscale"
    ok = is_allowed(request)
    hote = (request.headers.get("host") or "").split(":")[0]
    outils = []
    if ok:
        for o in d["outils"]:
            if o.get("actif") and d["wifi"].get(o["id"]) and (lan or o.get("lien_tailscale")):
                outils.append({"id": o["id"], "nom": o["nom"], "role": o.get("role", ""),
                               "lien": f"http://{hote}:{o['port']}" if lan else o.get("lien_tailscale")})
    return {"actif": d["actif"] or not lan, "entre": ok, "lan": lan,
            "dossiers": [{"id": x["id"], "nom": x["nom"], "mode": x["mode"]} for x in d["dossiers"]] if ok else [],
            "outils": outils}


@router.post("/api/invite/entrer")
async def enter(request: Request):
    d = read()
    ip = request.client.host if request.client else "?"
    if not d["actif"]:
        raise HTTPException(403, "L'accès invités est fermé.")
    if is_blocked(ip):
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    code = str((await request.json()).get("code", "")).strip()
    if not hmac.compare_digest(code, d["code"]):
        _failures.setdefault(ip, []).append(time.time())
        raise HTTPException(403, "Code incorrect.")
    r = JSONResponse({"ok": True})
    r.set_cookie(COOKIE, guest_cookie(d), max_age=COOKIE_LIFETIME, httponly=True, samesite="strict")
    activity_log.record("invite", f"Un invité est entré ({ip})")
    return r


@router.get("/api/invite/fichiers")
def list_files(request: Request, d: str, chemin: str = "/"):
    r = require_guest(request)
    dossier = _shared_folder(r, d)
    reel = _resolve(dossier["chemin"], chemin)
    if not os.path.isdir(reel):
        raise HTTPException(404, "Dossier introuvable")
    elements = []
    for nom in sorted(os.listdir(reel), key=str.lower):
        if nom.startswith("."):
            continue
        p = os.path.join(reel, nom)
        try:
            st = os.stat(p)
        except OSError:
            continue
        rel = (chemin.rstrip("/") + "/" + nom) if chemin != "/" else "/" + nom
        elements.append({"nom": nom, "chemin": rel, "dossier": os.path.isdir(p),
                         "taille": None if os.path.isdir(p) else st.st_size,
                         "modifie": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
    elements.sort(key=lambda e: (not e["dossier"], e["nom"].lower()))
    return {"dossier": {"id": dossier["id"], "nom": dossier["nom"], "mode": dossier["mode"]}, "chemin": chemin, "elements": elements}


@router.get("/api/invite/fichier")
def get_file(request: Request, d: str, chemin: str, voir: int = 0):
    r = require_guest(request)
    reel = _resolve(_shared_folder(r, d)["chemin"], chemin)
    if not os.path.isfile(reel):
        raise HTTPException(404, "Fichier introuvable")
    nom = os.path.basename(reel)
    import mimetypes
    type_ = mimetypes.guess_type(nom)[0] or "application/octet-stream"
    if voir and type_.startswith(("image/", "video/", "audio/")) and type_ != "image/svg+xml":
        return FileResponse(reel, media_type=type_, headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(nom)}"})
    return FileResponse(reel, filename=nom)


@router.post("/api/invite/envoyer")
async def upload(request: Request, d: str, chemin: str = "/"):
    r = require_guest(request)
    dossier = _shared_folder(r, d)
    if dossier["mode"] != "depot":
        raise HTTPException(403, "Ce dossier est en lecture seule.")
    cible = _resolve(dossier["chemin"], chemin)
    if not os.path.isdir(cible):
        raise HTTPException(404, "Dossier introuvable")
    formulaire = await request.form()
    envoyes = []
    for f in formulaire.getlist("fichiers"):
        if not isinstance(f, UploadFile) or not f.filename:
            continue
        nom = os.path.basename(f.filename.replace("\\", "/")).lstrip(".") or "fichier"
        base, ext = os.path.splitext(nom)
        reel, i = os.path.join(cible, nom), 2
        while os.path.exists(reel):   # jamais d'écrasement : « photo (2).jpg »
            reel, i = os.path.join(cible, f"{base} ({i}){ext}"), i + 1
        with open(reel, "wb") as sortie:
            shutil.copyfileobj(f.file, sortie, 1024 * 1024)
        try:
            os.chown(reel, 1000, os.stat(cible).st_gid or 1000)
        except OSError:
            pass
        envoyes.append(os.path.basename(reel))
    if envoyes:
        activity_log.record("invite", f"Invité : {len(envoyes)} fichier{'s' if len(envoyes) > 1 else ''} déposé{'s' if len(envoyes) > 1 else ''} dans {dossier['nom']}{chemin if chemin != '/' else ''}")
    return {"envoyes": envoyes}
