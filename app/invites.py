"""Accès invités : ce que voient les personnes connectées au Wi-Fi, sans compte.

Deux portes vers les mêmes pages invités :
- le Wi-Fi : un petit serveur séparé (port 80, http://<serveur>.local), qui
  ne connaît QUE les pages invités. La connexion admin y est impossible.
- Tailscale : l'appli, tant qu'on n'est pas connecté en admin.

Sur le Wi-Fi, il faut le code à 4 chiffres (ou le QR code qui le contient).
L'appareil est ensuite reconnu 7 jours, sauf si l'admin change le code.
Les invités ne voient que les dossiers et les outils choisis par l'admin.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import UploadFile

import journal

DOSSIER = os.environ.get("JABBA_DATA", "/data")
FICHIER = os.path.join(DOSSIER, "invites.json")
DOSSIER_INVITES = os.environ.get("JABBA_DOSSIER_INVITES", "/srv/data/invites")
COOKIE = "jabba_invite"
DUREE = 7 * 86400
STATIC = os.path.join(os.path.dirname(__file__), "static")
_verrou = threading.RLock()
_echecs = {}  # ip -> [horodatages]


# ---------------------------------------------------------------- réglages
def lire():
    try:
        with open(FICHIER) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("actif", False)
    d.setdefault("code", "%04d" % secrets.randbelow(10000))
    d.setdefault("generation", 1)
    d.setdefault("secret", secrets.token_hex(32))
    d.setdefault("dossiers", [{"id": "invites", "nom": "Invités", "chemin": DOSSIER_INVITES, "mode": "depot"}])
    d.setdefault("wifi", {"jellyfin": True})   # outils joignables depuis le Wi-Fi
    d.setdefault("outils", [])                 # copie tenue à jour par l'appli : {id, nom, role, port, actif}
    d.setdefault("adresse", None)              # adresse IP du serveur sur le Wi-Fi
    return d


def ecrire(d):
    with _verrou:
        os.makedirs(DOSSIER, exist_ok=True)
        tmp = FICHIER + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, FICHIER)


def modifier(fonction):
    with _verrou:
        d = lire()
        r = fonction(d)
        ecrire(d)
        return r


def nouveau_code():
    def f(d):
        d["code"] = "%04d" % secrets.randbelow(10000)
        d["generation"] += 1   # les invités déjà reconnus doivent ressaisir le code
        return d["code"]
    return modifier(f)


def preparer_dossier_invites():
    try:
        os.makedirs(DOSSIER_INVITES, exist_ok=True)
        st = os.stat(os.path.dirname(DOSSIER_INVITES))
        os.chown(DOSSIER_INVITES, 1000, st.st_gid or 1000)
    except OSError:
        pass


# ---------------------------------------------------------------- cookie invité
def _signer(d, contenu):
    return hmac.new(bytes.fromhex(d["secret"]), contenu.encode(), hashlib.sha256).hexdigest()[:32]


def cookie_invite(d):
    exp = int(time.time()) + DUREE
    contenu = f"{d['generation']}.{exp}"
    return f"{contenu}.{_signer(d, contenu)}"


def cookie_valide(d, valeur):
    try:
        gen, exp, sig = (valeur or "").split(".")
    except ValueError:
        return False
    return (hmac.compare_digest(sig, _signer(d, f"{gen}.{exp}")) and int(gen) == d["generation"]
            and int(exp) > time.time())


def bloque(ip):
    maintenant = time.time()
    _echecs[ip] = [t for t in _echecs.get(ip, []) if t > maintenant - 600]
    return len(_echecs[ip]) >= 8


# ---------------------------------------------------------------- fichiers
def _dossier(d, did):
    for x in d["dossiers"]:
        if x["id"] == did:
            return x
    raise HTTPException(404, "Dossier introuvable")


def _resoudre(base, chemin):
    base = os.path.realpath(base)
    morceaux = [m for m in (chemin or "").split("/") if m not in ("", ".")]
    if any(m == ".." or m.startswith(".") for m in morceaux):
        raise HTTPException(400, "Chemin invalide")
    reel = os.path.realpath(os.path.join(base, *morceaux))
    if reel != base and not reel.startswith(base + os.sep):
        raise HTTPException(400, "Chemin invalide")
    return reel


def autorise(request):
    if getattr(request.state, "via", "") == "tailscale":
        return True
    d = lire()
    return d["actif"] and cookie_valide(d, request.cookies.get(COOKIE))


def exiger(request):
    if not autorise(request):
        raise HTTPException(401, {"invite": True, "message": "Code d'accès demandé."})
    return lire()


routeur = APIRouter()


@routeur.get("/api/invite/etat")
def etat(request: Request):
    d = lire()
    lan = getattr(request.state, "via", "") != "tailscale"
    ok = autorise(request)
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


@routeur.post("/api/invite/entrer")
async def entrer(request: Request):
    d = lire()
    ip = request.client.host if request.client else "?"
    if not d["actif"]:
        raise HTTPException(403, "L'accès invités est fermé.")
    if bloque(ip):
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    code = str((await request.json()).get("code", "")).strip()
    if not hmac.compare_digest(code, d["code"]):
        _echecs.setdefault(ip, []).append(time.time())
        raise HTTPException(403, "Code incorrect.")
    r = JSONResponse({"ok": True})
    r.set_cookie(COOKIE, cookie_invite(d), max_age=DUREE, httponly=True, samesite="strict")
    journal.noter("invite", f"Un invité est entré ({ip})")
    return r


@routeur.get("/api/invite/fichiers")
def fichiers(request: Request, d: str, chemin: str = "/"):
    r = exiger(request)
    dossier = _dossier(r, d)
    reel = _resoudre(dossier["chemin"], chemin)
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


@routeur.get("/api/invite/fichier")
def fichier(request: Request, d: str, chemin: str, voir: int = 0):
    r = exiger(request)
    reel = _resoudre(_dossier(r, d)["chemin"], chemin)
    if not os.path.isfile(reel):
        raise HTTPException(404, "Fichier introuvable")
    nom = os.path.basename(reel)
    import mimetypes
    type_ = mimetypes.guess_type(nom)[0] or "application/octet-stream"
    if voir and type_.startswith(("image/", "video/", "audio/")) and type_ != "image/svg+xml":
        return FileResponse(reel, media_type=type_, headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(nom)}"})
    return FileResponse(reel, filename=nom)


@routeur.post("/api/invite/envoyer")
async def envoyer(request: Request, d: str, chemin: str = "/"):
    r = exiger(request)
    dossier = _dossier(r, d)
    if dossier["mode"] != "depot":
        raise HTTPException(403, "Ce dossier est en lecture seule.")
    cible = _resoudre(dossier["chemin"], chemin)
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
        journal.noter("invite", f"Invité : {len(envoyes)} fichier{'s' if len(envoyes) > 1 else ''} déposé{'s' if len(envoyes) > 1 else ''} dans {dossier['nom']}{chemin if chemin != '/' else ''}")
    return {"envoyes": envoyes}


# ---------------------------------------------------------------- la porte du Wi-Fi (port 80)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

RESEAUX_LOCAUX = [ipaddress.ip_network(n) for n in
                  ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8", "100.64.0.0/10")]


@app.middleware("http")
async def garde_wifi(request: Request, call_next):
    try:
        ip = ipaddress.ip_address((request.client.host if request.client else "").split("%")[0])
        local = any(ip in n for n in RESEAUX_LOCAUX)
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


app.include_router(routeur)


@app.get("/api/moi")
def moi_wifi():
    import socket
    from version import VERSION
    return {"mode": "invite", "serveur": socket.gethostname(), "version": VERSION, "admin": None}


@app.get("/api/{reste:path}")
@app.post("/api/{reste:path}")
def interdit(reste: str):
    raise HTTPException(404, "Indisponible sur le Wi-Fi : l'administration passe par Tailscale.")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
@app.get("/{reste:path}")
def page_wifi(reste: str = ""):
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})
