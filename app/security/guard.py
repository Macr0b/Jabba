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
async def garde(request: Request, call_next):
    if config.DEMO:
        request.state.user = request.headers.get("x-demo-login", "demo@exemple.fr")
        request.state.nom = "Alex"
    else:
        login = (request.headers.get("tailscale-user-login") or "").lower()
        if not login or login not in logins_autorises():
            if login and request.url.path.startswith("/api/"):
                refus_acces(login, request)
            return JSONResponse({"erreur": "Accès réservé à tes appareils Tailscale."}, status_code=403)
        request.state.user = login
        request.state.nom = request.headers.get("tailscale-user-name") or login
        if request.url.path == "/api/moi":
            visite(login, request.state.nom, request)
    request.state.via = "tailscale"
    # Connexion admin mémorisée sur cet appareil (cookie), sinon : mode invité.
    jeton = request.cookies.get(sessions.COOKIE)
    session = sessions.lire(jeton)
    request.state.session, request.state.jeton = session, jeton
    chemin = request.url.path
    if chemin.startswith("/api/") and not chemin.startswith(PUBLIC):
        if not session:
            return JSONResponse({"detail": {"admin": True, "message": "Connexion admin demandée."}}, status_code=401)
        if session.get("provisoire") and chemin not in PROVISOIRE_OK:
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


_CACHE_LOGINS = {"t": 0, "v": set()}


def logins_autorises():
    """Qui peut ouvrir l'appli : les comptes Tailscale du .env, et ceux reliés à un compte admin."""
    if time.time() - _CACHE_LOGINS["t"] > 5:
        _CACHE_LOGINS.update(t=time.time(), v=config.UTILISATEURS | accounts.logins_autorises())
    return _CACHE_LOGINS["v"]


_CACHE_APPAREILS = {"t": 0, "v": {}}


def appareil_de(request):
    """Nom de l'appareil Tailscale d'où vient la requête (d'après son adresse 100.x)."""
    ip = request.client.host if request.client else ""
    if time.time() - _CACHE_APPAREILS["t"] > 120:
        noms = {}
        try:
            code, sortie, _ = commands.hote(["tailscale", "status", "--json"], timeout=10)
            if code == 0:
                d = json.loads(sortie)
                for p in list((d.get("Peer") or {}).values()) + [d.get("Self") or {}]:
                    for a in p.get("TailscaleIPs") or []:
                        noms[a] = p.get("HostName")
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
        _CACHE_APPAREILS.update(t=time.time(), v=noms)
    return _CACHE_APPAREILS["v"].get(ip)


def visite(login, nom, request):
    try:
        if accounts.noter_visite(login, nom, appareil_de(request)):
            pass
    except OSError:
        pass


def refus_acces(login, request):
    try:
        appareil = appareil_de(request)
        if accounts.noter_refus(login, appareil):
            notifications.envoyer("securite", "Accès refusé", f"{login}{' (' + appareil + ')' if appareil else ''} a essayé d'ouvrir l'appli.",
                           "/#/utilisateurs", famille="admin", pour=pour_super())
    except OSError:
        pass


# Ce qui reste ouvert sans connexion admin (pages invités, connexion, mot de passe oublié).
PUBLIC = ("/api/moi", "/api/invite/", "/api/admin/connexion", "/api/admin/secours", "/api/comptes-oubli",
          "/api/comptes-remise", "/api/icone/")
PROVISOIRE_OK = {"/api/comptes-moi/mot-de-passe", "/api/admin/deconnexion"}


# ---------------------------------------------------------------- comptes admin
# Deux rangs : « super » (tout, dont la gestion des comptes) et « admin » (les
# actions sensibles). Le super admin principal est le compte du serveur.
def exiger_admin(request: Request, rang="admin", fort=False):
    """La session admin (identifiant, rang). `fort` : mot de passe saisi il y a moins de 10 minutes."""
    j = getattr(request.state, "session", None)
    if not j:
        raise HTTPException(401, {"admin": True, "message": "Connexion admin demandée."})
    if j.get("provisoire"):
        raise HTTPException(401, {"admin": True, "changer": True, "message": "Choisis d'abord ton nouveau mot de passe."})
    if rang == "super" and j["rang"] != "super":
        raise HTTPException(403, "Réservé au super admin.")
    if fort and not sessions.confirme_recemment(j):
        raise HTTPException(401, {"admin": True, "confirmer": True, "message": "Confirme ton mot de passe pour cette action."})
    request.state.admin = j["identifiant"]
    return j


def exiger_super(request: Request, fort=False):
    return exiger_admin(request, "super", fort)


def exiger_compte_serveur(request: Request):
    j = exiger_admin(request, "super", fort=True)
    if j["identifiant"] != nom_hote():
        raise HTTPException(403, "Réservé au compte du serveur.")
    return j


def admin_user():
    """Le compte du serveur dont le mot de passe est celui du super admin principal (par défaut : le premier compte créé)."""
    if config.ADMIN_USER or config.DEMO:
        return config.ADMIN_USER or "demo"
    _, sortie, _ = commands.hote(["getent", "passwd", "1000"])
    return sortie.split(":", 1)[0] or "root"


_NOM_HOTE = {}


def nom_hote():
    """Nom du serveur : c'est l'identifiant du super admin principal."""
    if "v" not in _NOM_HOTE:
        _NOM_HOTE["v"] = "serveur-demo" if config.DEMO else (commands.hote(["hostname"])[1].strip().lower() or "serveur")
    return _NOM_HOTE["v"]


def identifiants_serveur():
    """Identifiants qui désignent le compte du serveur."""
    return {nom_hote(), admin_user().lower(), "serveur"}


def verifier_admin(mot_de_passe):
    if config.DEMO:
        return mot_de_passe == "demo-mot-de-passe"
    return server_password.verifier_mot_de_passe(commands.hote, admin_user(), mot_de_passe)


def pour_super():
    """Comptes Tailscale des super admins (et appareils abonnés avant la v0.6)."""
    return config.UTILISATEURS | accounts.logins_super() | {None}


def verifier_identifiants(request, ident, mdp):
    """Le compte si identifiant et mot de passe sont bons, sinon une erreur (et l'échec est compté)."""
    if server_password.bloque():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    ident = (ident or nom_hote()).strip().lower()
    if ident in identifiants_serveur():
        if verifier_admin(mdp):
            return {"identifiant": nom_hote(), "nom": "Compte du serveur", "rang": "super", "provisoire": False}
    else:
        compte = accounts.verifier(ident, mdp)
        if compte:
            return compte
        server_password.echec()
    echec_admin(request, ident)
    raise HTTPException(403, "Identifiant ou mot de passe incorrect.")


def poser_session(reponse, request, compte):
    provisoire = bool(compte.get("provisoire"))
    jeton = sessions.creer(compte["identifiant"], compte["rang"], provisoire, appareil_de(request), getattr(request.state, "user", None))
    reponse.set_cookie(sessions.COOKIE, jeton, max_age=sessions.DUREE_PROVISOIRE if provisoire else sessions.DUREE,
                       httponly=True, secure=True, samesite="strict")
    return jeton


def infos_session(compte):
    return {"identifiant": compte["identifiant"], "rang": compte["rang"], "changer": bool(compte.get("provisoire")),
            "compte_serveur": compte["identifiant"] == nom_hote()}


def echec_admin(request, ident=""):
    activity_log.noter("admin", f"Connexion admin refusée{' : ' + ident if ident else ''}", request.state.nom)
    if server_password.bloque():
        notifications.envoyer("securite", "Mot de passe raté 5 fois",
                       f"Depuis l'appareil de {request.state.nom}. Les connexions admin sont bloquées 10 minutes.", "/#/systeme",
                       pour=pour_super())


def _changer(nouveau):
    if config.DEMO:
        if len(nouveau) < 10:
            raise HTTPException(400, "Le nouveau mot de passe doit faire au moins 10 caractères.")
        return
    try:
        server_password.changer_mot_de_passe(commands.hote_entree, admin_user(), nouveau)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------- comptes : gestion
def cmp(fonction):
    try:
        return fonction()
    except accounts.Refus as e:
        raise HTTPException(e.code, e.message)


def exiger_espace(request: Request):
    """L'espace Utilisateurs : tous les admins, ou le super admin seul selon le réglage."""
    j = exiger_admin(request)
    if j["rang"] != "super" and not accounts.espace_ouvert_aux_admins():
        raise HTTPException(403, "L'espace Utilisateurs est réservé au super admin.")
    return j
