"""security.guard"""
import json
import subprocess
import time
from security import server_password
from security import accounts
from security import sessions
from services import activity_log
from services import notifications
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
import config
from host import commands


# ---------------------------------------------------------------- sécurité
async def guard(request: Request, call_next):
    if config.DEMO:
        request.state.user = request.headers.get("x-demo-login", "demo@exemple.fr")
        request.state.nom = "Alex"
    else:
        login = (request.headers.get("tailscale-user-login") or "").lower()
        if not login or login not in allowed_logins():
            if login and request.url.path.startswith("/api/"):
                record_refusal(login, request)
            return JSONResponse({"erreur": "Accès réservé à tes appareils Tailscale."}, status_code=403)
        request.state.user = login
        request.state.nom = request.headers.get("tailscale-user-name") or login
        if request.url.path == "/api/moi":
            record_visit(login, request.state.nom, request)
    request.state.via = "tailscale"
    # Connexion admin mémorisée sur cet appareil (cookie), sinon : mode invité.
    jeton = request.cookies.get(sessions.COOKIE)
    session = sessions.read(jeton)
    request.state.session, request.state.jeton = session, jeton
    chemin = request.url.path
    if chemin.startswith("/api/") and not chemin.startswith(PUBLIC_PATHS):
        if not session:
            return JSONResponse({"detail": {"admin": True, "message": "Connexion admin demandée."}}, status_code=401)
        if session.get("provisoire") and chemin not in ALLOWED_WITH_TEMPORARY_PASSWORD:
            return JSONResponse({"detail": {"admin": True, "changer": True, "message": "Choisis d'abord ton nouveau mot de passe."}}, status_code=401)
    # Anti-CSRF : toute action doit venir de l'appli (en-tête impossible à
    # ajouter depuis un autre site sans autorisation CORS, jamais accordée).
    if request.method not in ("GET", "HEAD") and request.headers.get("x-jabba") != "1":
        return JSONResponse({"erreur": "Requête refusée."}, status_code=403)
    reponse = await call_next(request)
    reponse.headers.setdefault("X-Frame-Options", "DENY")
    reponse.headers["X-Content-Type-Options"] = "nosniff"
    reponse.headers["Referrer-Policy"] = "no-referrer"
    return reponse


_LOGINS_CACHE = {"t": 0, "v": set()}


def allowed_logins():
    """Qui peut ouvrir l'appli : les comptes Tailscale du .env, et ceux reliés à un compte admin."""
    if time.time() - _LOGINS_CACHE["t"] > 5:
        _LOGINS_CACHE.update(t=time.time(), v=config.TAILSCALE_USERS | accounts.allowed_logins())
    return _LOGINS_CACHE["v"]


_DEVICES_CACHE = {"t": 0, "v": {}}


def device_of(request):
    """Nom de l'appareil Tailscale d'où vient la requête (d'après son adresse 100.x)."""
    ip = request.client.host if request.client else ""
    if time.time() - _DEVICES_CACHE["t"] > 120:
        noms = {}
        try:
            code, sortie, _ = commands.run_on_host(["tailscale", "status", "--json"], timeout=10)
            if code == 0:
                d = json.loads(sortie)
                for p in list((d.get("Peer") or {}).values()) + [d.get("Self") or {}]:
                    for a in p.get("TailscaleIPs") or []:
                        noms[a] = p.get("HostName")
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
        _DEVICES_CACHE.update(t=time.time(), v=noms)
    return _DEVICES_CACHE["v"].get(ip)


def record_visit(login, nom, request):
    try:
        if accounts.record_visit(login, nom, device_of(request)):
            pass
    except OSError:
        pass


def record_refusal(login, request):
    try:
        appareil = device_of(request)
        if accounts.record_refusal(login, appareil):
            notifications.send("securite", "Accès refusé", f"{login}{' (' + appareil + ')' if appareil else ''} a essayé d'ouvrir l'appli.",
                           "/#/utilisateurs", famille="admin", pour=super_admin_logins())
    except OSError:
        pass


# Ce qui reste ouvert sans connexion admin (pages invités, connexion, mot de passe oublié).
PUBLIC_PATHS = ("/api/moi", "/api/invite/", "/api/admin/connexion", "/api/admin/secours", "/api/comptes-oubli",
          "/api/comptes-remise", "/api/icone/")
ALLOWED_WITH_TEMPORARY_PASSWORD = {"/api/comptes-moi/mot-de-passe", "/api/admin/deconnexion"}


# ---------------------------------------------------------------- comptes admin
# Deux rangs : « super » (tout, dont la gestion des comptes) et « admin » (les
# actions sensibles). Le super admin principal est le compte du serveur.
def require_admin(request: Request, rang="admin", fort=False):
    """La session admin (identifiant, rang). `fort` : mot de passe saisi il y a moins de 10 minutes."""
    j = getattr(request.state, "session", None)
    if not j:
        raise HTTPException(401, {"admin": True, "message": "Connexion admin demandée."})
    if j.get("provisoire"):
        raise HTTPException(401, {"admin": True, "changer": True, "message": "Choisis d'abord ton nouveau mot de passe."})
    if rang == "super" and j["rang"] != "super":
        raise HTTPException(403, "Réservé au super admin.")
    if fort and not sessions.confirmed_recently(j):
        raise HTTPException(401, {"admin": True, "confirmer": True, "message": "Confirme ton mot de passe pour cette action."})
    request.state.admin = j["identifiant"]
    return j


def require_super_admin(request: Request, fort=False):
    return require_admin(request, "super", fort)


def require_server_account(request: Request):
    j = require_admin(request, "super", fort=True)
    if j["identifiant"] != hostname():
        raise HTTPException(403, "Réservé au compte du serveur.")
    return j


def server_user():
    """Le compte du serveur dont le mot de passe est celui du super admin principal (par défaut : le premier compte créé)."""
    if config.ADMIN_USER or config.DEMO:
        return config.ADMIN_USER or "demo"
    _, sortie, _ = commands.run_on_host(["getent", "passwd", "1000"])
    return sortie.split(":", 1)[0] or "root"


_HOSTNAME_CACHE = {}


def hostname():
    """Nom du serveur : c'est l'identifiant du super admin principal."""
    if "v" not in _HOSTNAME_CACHE:
        _HOSTNAME_CACHE["v"] = "serveur-demo" if config.DEMO else (commands.run_on_host(["hostname"])[1].strip().lower() or "serveur")
    return _HOSTNAME_CACHE["v"]


def server_identifiers():
    """Identifiants qui désignent le compte du serveur."""
    return {hostname(), server_user().lower(), "serveur"}


def check_server_password(mot_de_passe):
    if config.DEMO:
        return mot_de_passe == "demo-mot-de-passe"
    return server_password.check_password(commands.run_on_host, server_user(), mot_de_passe)


def super_admin_logins():
    """Comptes Tailscale des super admins (et appareils abonnés avant la v0.6)."""
    return config.TAILSCALE_USERS | accounts.super_admin_logins() | {None}


def check_login(request, ident, mdp):
    """Le compte si identifiant et mot de passe sont bons, sinon une erreur (et l'échec est compté)."""
    if server_password.is_locked():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    ident = (ident or hostname()).strip().lower()
    if ident in server_identifiers():
        if check_server_password(mdp):
            return {"identifiant": hostname(), "nom": "Compte du serveur", "rang": "super", "provisoire": False}
    else:
        compte = accounts.check_credentials(ident, mdp)
        if compte:
            return compte
        server_password.record_failure()
    log_failed_login(request, ident)
    raise HTTPException(403, "Identifiant ou mot de passe incorrect.")


def open_session(reponse, request, compte):
    provisoire = bool(compte.get("provisoire"))
    jeton = sessions.create(compte["identifiant"], compte["rang"], provisoire, device_of(request), getattr(request.state, "user", None))
    reponse.set_cookie(sessions.COOKIE, jeton, max_age=sessions.TEMPORARY_LIFETIME if provisoire else sessions.LIFETIME,
                       httponly=True, secure=True, samesite="strict")
    return jeton


def session_info(compte):
    return {"identifiant": compte["identifiant"], "rang": compte["rang"], "changer": bool(compte.get("provisoire")),
            "compte_serveur": compte["identifiant"] == hostname()}


def log_failed_login(request, ident=""):
    activity_log.record("admin", f"Connexion admin refusée{' : ' + ident if ident else ''}", request.state.nom)
    if server_password.is_locked():
        notifications.send("securite", "Mot de passe raté 5 fois",
                       f"Depuis l'appareil de {request.state.nom}. Les connexions admin sont bloquées 10 minutes.", "/#/systeme",
                       pour=super_admin_logins())


def _change_server_password(nouveau):
    if config.DEMO:
        if len(nouveau) < 10:
            raise HTTPException(400, "Le nouveau mot de passe doit faire au moins 10 caractères.")
        return
    try:
        server_password.change_password(commands.run_on_host_with_input, server_user(), nouveau)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------- comptes : gestion
def translate_account_refusal(fonction):
    try:
        return fonction()
    except accounts.Refusal as e:
        raise HTTPException(e.code, e.message)


def require_admin_space(request: Request):
    """L'espace Utilisateurs : tous les admins, ou le super admin seul selon le réglage."""
    j = require_admin(request)
    if j["rang"] != "super" and not accounts.admin_space_open():
        raise HTTPException(403, "L'espace Utilisateurs est réservé au super admin.")
    return j
