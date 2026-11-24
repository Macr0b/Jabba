"""Contrôle d'accès : chaque demande passe ici avant d'arriver à une route.

Trois verrous, dans l'ordre :
  1. l'appareil Tailscale doit être autorisé (sinon : refus, noté et signalé) ;
  2. hors des chemins publics, une session admin est demandée ;
  3. toute action (POST…) doit venir de l'appli (en-tête X-Jabba) : protection contre
     les demandes envoyées par un autre site.

Sommaire :
  guard : le contrôle lui-même (branché dans main.py)
  allowed_logins, device_of, record_visit, record_refusal : qui ouvre l'appli
  require_admin, require_super_admin, require_server_account, require_admin_space :
      à appeler au début des routes réservées
  hostname, server_user, server_identifiers, check_server_password : le compte du serveur
  check_login, open_session, session_info, log_failed_login : connexion admin
  translate_account_refusal : erreurs de comptes -> réponses HTTP
  super_admin_logins  : comptes Tailscale des super admins (et appareils abonnés avant la v0.6)
  interne : _no_breach_lookup, _change_server_password
"""

import json
import subprocess
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

import config
from host import commands
from security import accounts, password_rules, server_password, sessions
from services import activity_log, notifications
from version import VERSION


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
            return JSONResponse({"error": "Accès réservé à tes appareils Tailscale."}, status_code=403)
        request.state.user = login
        request.state.nom = request.headers.get("tailscale-user-name") or login
        if request.url.path == "/api/moi":
            record_visit(login, request.state.nom, request)
    request.state.via = "tailscale"
    server_password.requester.set(request.state.user)  # les essais ratés se comptent par personne
    # Connexion admin mémorisée sur cet appareil (cookie), sinon : mode invité.
    token = request.cookies.get(sessions.COOKIE)
    session = sessions.read(token)
    request.state.session, request.state.jeton = session, token
    path = request.url.path
    if path.startswith("/api/") and not path.startswith(PUBLIC_PATHS):
        if not session:
            return JSONResponse({"detail": {"admin": True, "message": "Connexion admin demandée."}}, status_code=401)
        if session.get("temporary") and path not in ALLOWED_WITH_TEMPORARY_PASSWORD:
            return JSONResponse(
                {"detail": {"admin": True, "changer": True, "message": "Choisis d'abord ton nouveau mot de passe."}},
                status_code=401,
            )
    # Anti-CSRF : toute action doit venir de l'appli (en-tête impossible à
    # ajouter depuis un autre site sans autorisation CORS, jamais accordée).
    if request.method not in ("GET", "HEAD") and request.headers.get("x-jabba") != "1":
        return JSONResponse({"error": "Requête refusée."}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", PAGE_POLICY)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    # La version du serveur : une fenêtre ouverte avant une mise à jour se recharge toute seule (core/api.js).
    response.headers["X-Jabba-Version"] = VERSION
    return response


_LOGINS_CACHE = {"t": 0, "v": set()}


def allowed_logins():
    """Qui peut ouvrir l'appli : les comptes Tailscale du .env, et ceux reliés à un compte admin."""
    if time.time() - _LOGINS_CACHE["t"] > 5:
        _LOGINS_CACHE.update(t=time.time(), v=config.TAILSCALE_USERS | accounts.allowed_logins())
    return _LOGINS_CACHE["v"]


_DEVICES_CACHE = {"t": 0, "v": {}}


def device_of(request):
    """Nom de l'appareil Tailscale d'où vient la requête (d'après son adresse 100.x)."""
    client_address = request.client.host if request.client else ""
    if time.time() - _DEVICES_CACHE["t"] > 120:
        names = {}
        try:
            code, output, _ = commands.run_action("appareils_tailscale")
            if code == 0:
                status = json.loads(output)
                for peer in list((status.get("Peer") or {}).values()) + [status.get("Self") or {}]:
                    for address in peer.get("TailscaleIPs") or []:
                        names[address] = peer.get("HostName")
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
        _DEVICES_CACHE.update(t=time.time(), v=names)
    return _DEVICES_CACHE["v"].get(client_address)


def record_visit(login, name, request):
    try:
        if accounts.record_visit(login, name, device_of(request)):
            pass
    except OSError:
        pass


def record_refusal(login, request):
    try:
        device = device_of(request)
        if accounts.record_refusal(login, device):
            notifications.send(
                "securite",
                "Accès refusé",
                f"{login}{' (' + device + ')' if device else ''} a essayé d'ouvrir l'appli.",
                "/#/utilisateurs",
                family="admin",
                recipients=super_admin_logins(),
            )
    except OSError:
        pass


# Politique de sécurité des pages : seuls les scripts de l'appli elle-même peuvent tourner
# (aucun script venu d'ailleurs, aucun script écrit dans la page). Les outils ouverts dans
# l'appli (Jellyfin…) s'affichent dans un cadre : seulement en https.
PAGE_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "media-src 'self' blob:; connect-src 'self'; worker-src 'self' blob:; frame-src 'self' https:; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)

# Ce qui reste ouvert sans connexion admin (pages invités, connexion, mot de passe oublié).
PUBLIC_PATHS = (
    "/api/moi",
    "/api/invite/",
    "/api/admin/connexion",
    "/api/admin/secours",
    "/api/admin/verification",  # 2e étape de la connexion (double vérification)
    "/api/admin/cle/options",
    "/api/comptes-oubli",
    "/api/comptes-remise",
    "/api/icone/",
)
ALLOWED_WITH_TEMPORARY_PASSWORD = {"/api/comptes-moi/mot-de-passe", "/api/admin/deconnexion"}


# ---------------------------------------------------------------- comptes admin
# Deux rangs : « super » (tout, dont la gestion des comptes) et « admin » (les
# actions sensibles). Le super admin principal est le compte du serveur.
def require_admin(request: Request, rank="admin", strong=False):
    """La session admin (identifiant, rang). `fort` : mot de passe saisi il y a moins de 10 minutes."""
    session = getattr(request.state, "session", None)
    if not session:
        raise HTTPException(401, {"admin": True, "message": "Connexion admin demandée."})
    if session.get("temporary"):
        raise HTTPException(
            401, {"admin": True, "changer": True, "message": "Choisis d'abord ton nouveau mot de passe."}
        )
    if rank == "super" and session["rank"] != "super":
        raise HTTPException(403, "Réservé au super admin.")
    if strong and not sessions.confirmed_recently(session):
        raise HTTPException(
            401, {"admin": True, "confirmer": True, "message": "Confirme ton mot de passe pour cette action."}
        )
    request.state.admin = session["username"]
    return session


def require_super_admin(request: Request, strong=False):
    return require_admin(request, "super", strong)


def require_server_account(request: Request):
    session = require_admin(request, "super", strong=True)
    if session["username"] != hostname():
        raise HTTPException(403, "Réservé au compte du serveur.")
    return session


def server_user():
    """Le compte du serveur dont le mot de passe est celui du super admin principal (par défaut : le premier compte créé)."""
    if config.ADMIN_USER or config.DEMO:
        return config.ADMIN_USER or "demo"
    _, output, _ = commands.run_action("compte_serveur")
    return output.split(":", 1)[0] or "root"


_HOSTNAME_CACHE = {}


def hostname():
    """Nom du serveur : c'est l'identifiant du super admin principal."""
    if "v" not in _HOSTNAME_CACHE:
        _HOSTNAME_CACHE["v"] = (
            "serveur-demo" if config.DEMO else (commands.run_action("nom_du_serveur")[1].strip().lower() or "serveur")
        )
    return _HOSTNAME_CACHE["v"]


def server_identifiers():
    """Identifiants qui désignent le compte du serveur."""
    return {hostname(), server_user().lower(), "serveur"}


def check_server_password(password):
    if config.DEMO:
        if password == "demo-mot-de-passe":
            return True
        server_password.record_failure()  # comme sur le serveur : l'erreur compte
        return False
    return server_password.check_password(server_user(), password)


def super_admin_logins():
    """Comptes Tailscale des super admins (et appareils abonnés avant la v0.6)."""
    return config.TAILSCALE_USERS | accounts.super_admin_logins() | {None}


def check_login(request, identifier, password):
    """Le compte si identifiant et mot de passe sont bons, sinon une erreur (et l'échec est compté)."""
    if server_password.is_locked():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    identifier = (identifier or hostname()).strip().lower()
    if identifier in server_identifiers():
        if check_server_password(password):
            return {"username": hostname(), "name": "Compte du serveur", "rank": "super", "temporary": False}
    else:
        account = accounts.check_credentials(identifier, password)
        if account:
            return account
        server_password.record_failure()
    log_failed_login(request, identifier)
    raise HTTPException(403, "Identifiant ou mot de passe incorrect.")


def open_session(response, request, account):
    temporary = bool(account.get("temporary"))
    token = sessions.create(
        account["username"], account["rank"], temporary, device_of(request), getattr(request.state, "user", None)
    )
    response.set_cookie(
        sessions.COOKIE,
        token,
        max_age=sessions.TEMPORARY_LIFETIME if temporary else sessions.LIFETIME,
        httponly=True,
        secure=True,
        samesite="strict",
    )
    return token


def session_info(account):
    return {
        "username": account["username"],
        "rank": account["rank"],
        "changer": bool(account.get("temporary")),
        "compte_serveur": account["username"] == hostname(),
    }


def log_failed_login(request, identifier=""):
    activity_log.record(
        "admin", f"Connexion admin refusée{' : ' + identifier if identifier else ''}", request.state.nom
    )
    if server_password.is_locked():
        notifications.send(
            "securite",
            "Mot de passe raté 5 fois",
            f"Depuis l'appareil de {request.state.nom}. Les connexions admin sont bloquées 10 minutes.",
            "/#/systeme",
            recipients=super_admin_logins(),
        )


def _no_breach_lookup(prefix):
    """Mode démo : pas d'appel à Internet."""
    return ""


def _change_server_password(new_value):
    if config.DEMO:
        try:
            password_rules.check(new_value, (server_user(),), fetch=_no_breach_lookup)
        except ValueError as error:
            raise HTTPException(400, str(error))
        return
    try:
        server_password.change_password(server_user(), new_value)
    except ValueError as error:
        raise HTTPException(400, str(error))
    except RuntimeError as error:
        raise HTTPException(500, str(error))


# ---------------------------------------------------------------- comptes : gestion
def translate_account_refusal(function):
    try:
        return function()
    except accounts.Refusal as error:
        raise HTTPException(error.code, error.message)


def require_admin_space(request: Request):
    """L'espace Utilisateurs : tous les admins, ou le super admin seul selon le réglage."""
    session = require_admin(request)
    if session["rank"] != "super" and not accounts.admin_space_open():
        raise HTTPException(403, "L'espace Utilisateurs est réservé au super admin.")
    return session
