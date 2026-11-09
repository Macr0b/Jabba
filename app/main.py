"""Appli Jabba — le moteur.

Tourne sur Jabba (réseau de l'hôte, port 127.0.0.1:8090) et n'est joignable
que par `tailscale serve`, qui ajoute l'identité Tailscale de la personne.
Les commandes système sont lancées sur Jabba lui-même via nsenter.
"""
import asyncio
import base64
import json
import mimetypes
import os
import re
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote, unquote, urljoin

import httpx

import admin
import comptes
import invites
import secrets
import lecture
import sessions
import terminal
import disque
import disques_auto
import journal
import maj
import notifs
from fastapi import FastAPI, HTTPException, Request, WebSocket
from starlette.datastructures import UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from xml.sax.saxutils import escape as xml_escape
from fastapi.staticfiles import StaticFiles

from version import VERSION  # noqa: E402
DEMO = os.environ.get("JABBA_DEMO") == "1"
UTILISATEURS = {u.strip().lower() for u in os.environ.get("TS_USERS", "").split(",") if u.strip()}
NC_URL = os.environ.get("NC_URL", "http://127.0.0.1:8081").rstrip("/")
NC_HOST = os.environ.get("NC_HOST", "")
NC_USER = os.environ.get("NC_USER", "")
NC_PASS = os.environ.get("NC_APP_PASSWORD", "")
NC_PUBLIC = os.environ.get("NC_PUBLIC_URL", "").rstrip("/")
STATIC = os.path.join(os.path.dirname(__file__), "static")
ADMIN_USER = os.environ.get("ADMIN_USER", "").strip()
DEPOT = os.environ.get("JABBA_DEPOT", "").strip()
SEUIL_BATTERIE = int(os.environ.get("SEUIL_BATTERIE", "15"))


def lire_liens():
    liens = {}
    for morceau in os.environ.get("LIENS", "").split(","):
        if "=" in morceau:
            nom, url = morceau.split("=", 1)
            liens[nom.strip()] = url.strip()
    return liens


LIENS = lire_liens()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(invites.routeur)   # pages invités : ouvertes tant qu'on n'est pas connecté en admin


# ---------------------------------------------------------------- sécurité
@app.middleware("http")
async def garde(request: Request, call_next):
    if DEMO:
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
        _CACHE_LOGINS.update(t=time.time(), v=UTILISATEURS | comptes.logins_autorises())
    return _CACHE_LOGINS["v"]


_CACHE_APPAREILS = {"t": 0, "v": {}}


def appareil_de(request):
    """Nom de l'appareil Tailscale d'où vient la requête (d'après son adresse 100.x)."""
    ip = request.client.host if request.client else ""
    if time.time() - _CACHE_APPAREILS["t"] > 120:
        noms = {}
        try:
            code, sortie, _ = hote(["tailscale", "status", "--json"], timeout=10)
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
        if comptes.noter_visite(login, nom, appareil_de(request)):
            pass
    except OSError:
        pass


def refus_acces(login, request):
    try:
        appareil = appareil_de(request)
        if comptes.noter_refus(login, appareil):
            notifs.envoyer("securite", "Accès refusé", f"{login}{' (' + appareil + ')' if appareil else ''} a essayé d'ouvrir l'appli.",
                           "/#/utilisateurs", famille="admin", pour=pour_super())
    except OSError:
        pass


# Ce qui reste ouvert sans connexion admin (pages invités, connexion, mot de passe oublié).
PUBLIC = ("/api/moi", "/api/invite/", "/api/admin/connexion", "/api/admin/secours", "/api/comptes-oubli",
          "/api/comptes-remise", "/api/icone/")
PROVISOIRE_OK = {"/api/comptes-moi/mot-de-passe", "/api/admin/deconnexion"}


# ---------------------------------------------------------------- hôte
def hote(cmd, timeout=30, entree=None):
    """Lance une commande sur le serveur (pas dans le conteneur)."""
    complet = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--"] + cmd
    r = subprocess.run(complet, capture_output=True, text=True, timeout=timeout, input=entree)
    return r.returncode, r.stdout, r.stderr


def hote_entree(cmd, entree, timeout=30):
    return hote(cmd, timeout=timeout, entree=entree)


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
    if ADMIN_USER or DEMO:
        return ADMIN_USER or "demo"
    _, sortie, _ = hote(["getent", "passwd", "1000"])
    return sortie.split(":", 1)[0] or "root"


_NOM_HOTE = {}


def nom_hote():
    """Nom du serveur : c'est l'identifiant du super admin principal."""
    if "v" not in _NOM_HOTE:
        _NOM_HOTE["v"] = "serveur-demo" if DEMO else (hote(["hostname"])[1].strip().lower() or "serveur")
    return _NOM_HOTE["v"]


def identifiants_serveur():
    """Identifiants qui désignent le compte du serveur."""
    return {nom_hote(), admin_user().lower(), "serveur"}


def verifier_admin(mot_de_passe):
    if DEMO:
        return mot_de_passe == "demo-mot-de-passe"
    return admin.verifier_mot_de_passe(hote, admin_user(), mot_de_passe)


def pour_super():
    """Comptes Tailscale des super admins (et appareils abonnés avant la v0.6)."""
    return UTILISATEURS | comptes.logins_super() | {None}


def verifier_identifiants(request, ident, mdp):
    """Le compte si identifiant et mot de passe sont bons, sinon une erreur (et l'échec est compté)."""
    if admin.bloque():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    ident = (ident or nom_hote()).strip().lower()
    if ident in identifiants_serveur():
        if verifier_admin(mdp):
            return {"identifiant": nom_hote(), "nom": "Compte du serveur", "rang": "super", "provisoire": False}
    else:
        compte = comptes.verifier(ident, mdp)
        if compte:
            return compte
        admin.echec()
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


@app.post("/api/admin/connexion")
async def api_admin_connexion(request: Request):
    corps = await request.json()
    compte = verifier_identifiants(request, corps.get("identifiant"), corps.get("mot_de_passe", ""))
    provisoire = bool(compte.get("provisoire"))
    journal.noter("admin", f"Connexion admin : {compte['identifiant']}" + (" (mot de passe provisoire)" if provisoire else ""), request.state.nom)
    sessions.fermer(request.state.jeton)
    r = JSONResponse(infos_session(compte))
    poser_session(r, request, compte)
    return r


@app.post("/api/admin/confirmer")
async def api_admin_confirmer(request: Request):
    """Redemander le mot de passe avant une action sensible."""
    j = exiger_admin(request)
    verifier_identifiants(request, j["identifiant"], (await request.json()).get("mot_de_passe", ""))
    sessions.confirmer(request.state.jeton)
    return {"ok": True}


@app.post("/api/admin/deconnexion")
def api_admin_deconnexion(request: Request):
    sessions.fermer(request.state.jeton)
    r = JSONResponse({"ok": True})
    r.delete_cookie(sessions.COOKIE, secure=True, httponly=True, samesite="strict")
    return r


@app.get("/api/sessions")
def api_sessions(request: Request):
    j = exiger_admin(request)
    actuelle = j["id"]
    toutes = sessions.lister()
    if j["rang"] != "super":
        toutes = [x for x in toutes if x["identifiant"] == j["identifiant"]]
    return [{**x, "actuelle": x["id"] == actuelle} for x in toutes]


@app.post("/api/sessions/{sid}/fermer")
def api_sessions_fermer(sid: str, request: Request):
    j = exiger_admin(request)
    cible = next((x for x in sessions.lister() if x["id"] == sid), None)
    if not cible:
        raise HTTPException(404, "Appareil introuvable.")
    if cible["identifiant"] != j["identifiant"] and j["rang"] != "super":
        raise HTTPException(403, "Tu ne peux déconnecter que tes propres appareils.")
    sessions.fermer_id(sid)
    journal.noter("admin", f"Appareil déconnecté : {cible['identifiant']}{' (' + cible['appareil'] + ')' if cible.get('appareil') else ''}, par {j['identifiant']}", request.state.nom)
    return {"ok": True}


def echec_admin(request, ident=""):
    journal.noter("admin", f"Connexion admin refusée{' : ' + ident if ident else ''}", request.state.nom)
    if admin.bloque():
        notifs.envoyer("securite", "Mot de passe raté 5 fois",
                       f"Depuis l'appareil de {request.state.nom}. Les connexions admin sont bloquées 10 minutes.", "/#/systeme",
                       pour=pour_super())


@app.get("/api/admin/etat")
def api_admin_etat(request: Request):
    j = request.state.session
    return {"code_existe": admin.code_existe(), "utilisateur": admin_user(), "serveur": nom_hote(),
            "connecte": {"identifiant": j["identifiant"], "rang": j["rang"]} if j else None}


def _changer(nouveau):
    if DEMO:
        if len(nouveau) < 10:
            raise HTTPException(400, "Le nouveau mot de passe doit faire au moins 10 caractères.")
        return
    try:
        admin.changer_mot_de_passe(hote_entree, admin_user(), nouveau)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))


@app.post("/api/admin/mot-de-passe")
async def api_admin_mdp(request: Request):
    """Mot de passe du compte du serveur (super admin principal) : c'est aussi celui de sudo."""
    corps = await request.json()
    if admin.bloque():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    if not verifier_admin(corps.get("ancien", "")):
        echec_admin(request, nom_hote())
        raise HTTPException(403, "L'ancien mot de passe est incorrect.")
    _changer(corps.get("nouveau", ""))
    sessions.fermer_compte(nom_hote())   # ses appareils devront se reconnecter avec le nouveau
    notifs.envoyer("securite", "Mot de passe du serveur changé", f"Par {request.state.nom}.", "/#/systeme", famille="admin", pour=pour_super())
    return {"ok": True}


@app.post("/api/admin/code")
def api_admin_code(request: Request):
    exiger_compte_serveur(request)
    journal.noter("admin", "Nouveau code de secours créé", request.state.nom)
    return {"code": admin.generer_code()}


@app.post("/api/admin/secours")
async def api_admin_secours(request: Request):
    corps = await request.json()
    if admin.bloque():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    if not admin.verifier_code(corps.get("code", "")):
        echec_admin(request, "code de secours")
        raise HTTPException(403, "Code de secours incorrect.")
    _changer(corps.get("nouveau", ""))
    sessions.fermer_compte(nom_hote())
    notifs.envoyer("securite", "Mot de passe du serveur changé", f"Avec le code de secours, par {request.state.nom}.", "/#/systeme",
                   famille="admin", pour=pour_super())
    return {"ok": True, "code": admin.generer_code()}


# ---------------------------------------------------------------- comptes : gestion
def cmp(fonction):
    try:
        return fonction()
    except comptes.Refus as e:
        raise HTTPException(e.code, e.message)


def exiger_espace(request: Request):
    """L'espace Utilisateurs : tous les admins, ou le super admin seul selon le réglage."""
    j = exiger_admin(request)
    if j["rang"] != "super" and not comptes.espace_ouvert_aux_admins():
        raise HTTPException(403, "L'espace Utilisateurs est réservé au super admin.")
    return j


@app.get("/api/comptes")
def api_comptes(request: Request):
    j = exiger_espace(request)
    d = comptes.lister()
    serveur = {"id": "serveur", "identifiant": nom_hote(), "nom": "Compte du serveur", "rang": "super",
               "tailscale": None, "systeme": True}
    return {"moi": {"identifiant": j["identifiant"], "rang": j["rang"]}, "comptes": [serveur] + d["comptes"],
            "reglages": d["reglages"], "demandes": d["demandes"] if j["rang"] == "super" else [],
            "demandes_n": len(d["demandes"]), "proprietaires": sorted(UTILISATEURS), **comptes.connexions()}


@app.post("/api/comptes")
async def api_comptes_creer(request: Request):
    j = exiger_espace(request)
    corps = await request.json()
    compte, mdp = cmp(lambda: comptes.creer(corps.get("identifiant"), corps.get("nom"), corps.get("rang", "admin"),
                                            corps.get("tailscale"), j["identifiant"], j["rang"], identifiants_serveur()))
    journal.noter("admin", f"Compte ajouté : {compte['identifiant']} ({'super admin' if compte['rang'] == 'super' else 'admin'}), par {j['identifiant']}", request.state.nom)
    notifs.envoyer("comptes", "Compte ajouté", f"{compte['nom']} ({compte['identifiant']}), par {j['identifiant']}.", "/#/utilisateurs",
                   famille="admin", pour=pour_super(), noter=False)
    if compte["tailscale"]:
        notifs.envoyer("comptes", "Ton compte admin est prêt", "Ouvre l'appli Jabba : ton mot de passe provisoire s'affiche.", "/",
                       pour={compte["tailscale"]}, noter=False, forcer=True)
        return {"compte": compte, "remis": True}
    return {"compte": compte, "mot_de_passe": mdp}


@app.post("/api/comptes/{cid}")
async def api_comptes_modifier(cid: str, request: Request):
    j = exiger_espace(request)
    corps = await request.json()
    action = corps.get("action")
    if action in ("supprimer", "rang", "terminal"):
        exiger_admin(request, fort=True)
    if action == "supprimer":
        c = cmp(lambda: comptes.supprimer(cid, j["rang"]))
        sessions.fermer_compte(c["identifiant"])
        texte = f"Compte retiré : {c['identifiant']}"
    elif action == "rang":
        c = cmp(lambda: comptes.modifier(cid, j["rang"], rang=corps.get("rang")))
        sessions.fermer_compte(c["identifiant"])
        texte = f"{c['identifiant']} est maintenant {'super admin' if c['rang'] == 'super' else 'admin'}"
    elif action == "tailscale":
        c = cmp(lambda: comptes.modifier(cid, j["rang"], tailscale=corps.get("tailscale") or None))
        texte = f"{c['identifiant']} : compte Tailscale {c['tailscale'] or 'retiré'}"
    elif action == "terminal":
        c = cmp(lambda: comptes.modifier(cid, j["rang"], terminal=bool(corps.get("terminal"))))
        texte = f"Terminal {'autorisé' if c['terminal'] else 'retiré'} pour {c['identifiant']}"
    elif action == "nom":
        c = cmp(lambda: comptes.modifier(cid, j["rang"], nom=corps.get("nom") or ""))
        texte = f"{c['identifiant']} renommé « {c['nom']} »"
    else:
        raise HTTPException(400, "Action inconnue.")
    journal.noter("admin", f"{texte}, par {j['identifiant']}", request.state.nom)
    if action in ("supprimer", "rang", "terminal"):
        notifs.envoyer("comptes", "Comptes modifiés", f"{texte}, par {j['identifiant']}.", "/#/utilisateurs",
                       famille="admin", pour=pour_super(), noter=False)
    return {"ok": True, "compte": c}


@app.post("/api/comptes-reglages")
async def api_comptes_reglages(request: Request):
    j = exiger_super(request, fort=True)
    ouvert = bool((await request.json()).get("espace_admins"))
    comptes.regler(ouvert)
    journal.noter("admin", f"Espace Utilisateurs {'ouvert à tous les admins' if ouvert else 'réservé au super admin'}, par {j['identifiant']}", request.state.nom)
    return {"ok": True}


@app.post("/api/comptes-moi/mot-de-passe")
async def api_comptes_mien(request: Request):
    """Un admin (compte de l'appli) change son propre mot de passe. Accepté avec une session provisoire."""
    j = request.state.session
    if not j or j["identifiant"] == nom_hote():
        raise HTTPException(401, {"admin": True, "message": "Connexion admin demandée."})
    corps = await request.json()
    if not j.get("provisoire") and not comptes.verifier(j["identifiant"], corps.get("ancien", "")):
        raise HTTPException(403, "L'ancien mot de passe est incorrect.")
    cmp(lambda: comptes.changer_mien(j["identifiant"], corps.get("nouveau", "")))
    sessions.fermer_compte(j["identifiant"])   # déconnecte ses autres appareils
    compte = comptes.trouver(j["identifiant"])
    journal.noter("admin", f"{j['identifiant']} a choisi un nouveau mot de passe", request.state.nom)
    r = JSONResponse(infos_session({**compte, "provisoire": False}))
    poser_session(r, request, {**compte, "provisoire": False})
    return r


# ---------------------------------------------------------------- comptes : mot de passe oublié
@app.post("/api/comptes-oubli")
async def api_comptes_oubli(request: Request):
    corps = await request.json()
    if admin.bloque():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    ident = (corps.get("identifiant") or "").strip().lower()
    if ident in identifiants_serveur():
        return {"ok": True, "serveur": True}
    c = comptes.demander(ident, request.state.user, request.state.nom)
    if c:
        journal.noter("admin", f"Mot de passe oublié : demande pour {c['identifiant']}", request.state.nom)
        notifs.envoyer("comptes", "Demande de mot de passe", f"{c['nom']} ({c['identifiant']}) a oublié son mot de passe. Ouvre l'appli pour accepter ou refuser.",
                       "/#/utilisateurs", famille="admin", pour=pour_super(), noter=False)
    else:
        admin.echec()
    # Même réponse dans tous les cas : on ne dit pas si l'identifiant existe.
    return {"ok": True}


@app.post("/api/comptes-demandes/{did}")
async def api_comptes_repondre(did: str, request: Request):
    j = exiger_super(request, fort=True)
    accepter = bool((await request.json()).get("accepter"))
    dem, _ = cmp(lambda: comptes.repondre(did, accepter, j["identifiant"]))
    sessions.fermer_compte(dem["identifiant"])
    journal.noter("admin", f"Demande de {dem['identifiant']} {'acceptée' if accepter else 'refusée'}, par {j['identifiant']}", request.state.nom)
    if accepter:
        notifs.envoyer("comptes", "Demande acceptée", "Ouvre l'appli Jabba : ton mot de passe provisoire s'affiche.", "/",
                       pour={dem.get("remis_a") or dem["login"]}, noter=False, forcer=True)
    else:
        notifs.envoyer("comptes", "Demande refusée", "Vois avec le super admin.", "/", pour={dem["login"]}, noter=False, forcer=True)
    return {"ok": True}


@app.post("/api/comptes-remise")
def api_comptes_remise(request: Request):
    """Le mot de passe provisoire, une seule fois, sur l'appareil de la personne concernée."""
    r = comptes.prendre_remise(request.state.user)
    if not r:
        raise HTTPException(404, "Rien à récupérer.")
    journal.noter("admin", f"Mot de passe provisoire affiché pour {r['identifiant']}", request.state.nom)
    return r


def hote_sh(script, timeout=30):
    return hote(["sh", "-c", script], timeout)


# ---------------------------------------------------------------- tâches longues
TACHES = {}


def lancer_tache(titre, script, qui=None, outil=None):
    tid = uuid.uuid4().hex[:8]
    TACHES[tid] = {"id": tid, "titre": titre, "etat": "en cours", "debut": time.time(), "journal": "", "outil": outil}

    def travail():
        try:
            if DEMO:
                time.sleep(3)
                code, sortie, err = 0, "ok", ""
            else:
                code, sortie, err = hote_sh(script, timeout=1800)
            TACHES[tid]["journal"] = (sortie + err)[-4000:]
            TACHES[tid]["etat"] = "terminé" if code == 0 else "erreur"
        except Exception as e:  # noqa: BLE001
            TACHES[tid]["journal"] = str(e)
            TACHES[tid]["etat"] = "erreur"
        TACHES[tid]["fin"] = time.time()
        journal.noter("outil", titre + (" : terminé" if TACHES[tid]["etat"] == "terminé" else " : erreur"), qui)

    threading.Thread(target=travail, daemon=True).start()
    return TACHES[tid]


@app.get("/api/taches/{tid}")
def tache(tid: str):
    if tid not in TACHES:
        raise HTTPException(404, "Tâche inconnue")
    return TACHES[tid]


# ---------------------------------------------------------------- mesures
def cpu_pourcent():
    def lire():
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:]]
        return sum(v), v[3] + v[4]

    t1, i1 = lire()
    time.sleep(0.3)
    t2, i2 = lire()
    return round(100 * (1 - (i2 - i1) / max(1, t2 - t1)))


def memoire():
    info = {}
    with open("/proc/meminfo") as f:
        for ligne in f:
            cle, val = ligne.split(":")
            info[cle] = int(val.split()[0]) * 1024
    return {"total": info["MemTotal"], "utilise": info["MemTotal"] - info["MemAvailable"]}


def temperature():
    meilleure = None
    base = "/sys/class/thermal"
    try:
        for zone in os.listdir(base):
            if not zone.startswith("thermal_zone"):
                continue
            try:
                type_ = open(f"{base}/{zone}/type").read().strip()
                t = int(open(f"{base}/{zone}/temp").read()) / 1000
            except OSError:
                continue
            if type_ == "x86_pkg_temp":
                return round(t)
            if meilleure is None or t > meilleure:
                meilleure = t
    except OSError:
        pass
    return round(meilleure) if meilleure is not None else None


def espace(chemins):
    code, sortie, _ = hote(["df", "-B1", "--output=target,size,used"] + chemins)
    res = {}
    for ligne in sortie.splitlines()[1:]:
        p = ligne.split()
        if len(p) == 3:
            res[p[0]] = {"total": int(p[1]), "utilise": int(p[2])}
    return res


def duree_marche():
    with open("/proc/uptime") as f:
        return int(float(f.read().split()[0]))


# ---------------------------------------------------------------- outils (Docker)
NOMS = {
    "jellyfin": ("Jellyfin", "Films et séries"),
    "nextcloud": ("Nextcloud", "Fichiers, photos, agenda"),
    "dockge": ("Dockge", "Gestion avancée des outils"),
    "jabba-app": ("Appli Jabba", "Cette appli"),
    "adresses": ("Adresses", "Les adresses https des outils"),
}
# Outils du système : toujours actifs, sans interrupteur.
SYSTEME = {"jabba-app", "adresses"}
# Conteneur « adresse » (Tailscale) qui suit l'interrupteur de chaque outil.
ADRESSES = {
    "nextcloud": "adresse-nextcloud",
    "jellyfin": "adresse-jellyfin",
    "dockge": "adresse-dockge",
}
# Où chaque outil range son icône : l'appli affiche le logo servi par l'outil lui-même.
# Port local de chaque outil : l'appli y va chercher le logo que l'outil fournit lui-même.
PORTS = {"jellyfin": 8096, "nextcloud": 8081, "dockge": 5001}
ICONES_SECOURS = ["/web/favicon.png", "/web/favicon.ico", "/favicon.ico", "/favicon.png",
                  "/static/img/icons/favicon-32x32.png", "/core/img/favicon.ico", "/icon.svg"]
HORS_APPLI = {"nextcloud"}


def lister_outils():
    if DEMO:
        from demo import OUTILS
        return OUTILS
    code, sortie, err = hote(["docker", "ps", "-a", "--format", "{{json .}}"])
    if code != 0:
        raise HTTPException(500, "Docker ne répond pas : " + err[:200])
    projets = {}
    for ligne in sortie.splitlines():
        c = json.loads(ligne)
        etiquettes = dict(e.split("=", 1) for e in c.get("Labels", "").split(",") if "=" in e)
        projet = etiquettes.get("com.docker.compose.project", c["Names"])
        dossier = etiquettes.get("com.docker.compose.project.working_dir", "")
        p = projets.setdefault(projet, {"id": projet, "dossier": dossier, "conteneurs": []})
        p["conteneurs"].append({"nom": c["Names"], "etat": c["State"], "statut": c["Status"]})
    resultat = []
    for pid, p in sorted(projets.items()):
        nom, role = NOMS.get(pid, (pid, ""))
        etats = {c["etat"] for c in p["conteneurs"]}
        if etats == {"running"}:
            etat = "actif"
        elif "running" in etats:
            etat = "partiel"
        else:
            etat = "arrêté"
        if any("restarting" in c["etat"] for c in p["conteneurs"]):
            etat = "en panne"
        resultat.append({
            "id": pid, "nom": nom, "role": role, "etat": etat,
            "lien": LIENS.get(pid), "dossier": p["dossier"], "conteneurs": p["conteneurs"],
            "modifiable": pid != "jabba-app" and bool(p["dossier"]),
            "systeme": pid in SYSTEME, "icone": pid in PORTS,
            "dans_appli": pid not in HORS_APPLI,
        })
    return resultat


def outil(pid):
    for o in lister_outils():
        if o["id"] == pid:
            if not o["modifiable"]:
                raise HTTPException(400, "Cet outil ne se gère pas depuis l'appli.")
            if not re.fullmatch(r"/[\w./-]+", o["dossier"]):
                raise HTTPException(400, "Dossier inattendu.")
            return o
    raise HTTPException(404, "Outil inconnu")


@app.get("/api/outils")
def api_outils():
    return lister_outils()


CACHE_ICONES = {}
RE_LIEN = re.compile(r"<link[^>]+>", re.I)


def chercher_icone(pid):
    base = f"http://127.0.0.1:{PORTS[pid]}/"
    with httpx.Client(timeout=5, follow_redirects=True) as c:
        candidats = []
        try:
            page = c.get(base)
            for balise in RE_LIEN.findall(page.text):
                rel = re.search(r'rel=["\']([^"\']+)', balise, re.I)
                href = re.search(r'href=["\']([^"\']+)', balise, re.I)
                if rel and href and "icon" in rel.group(1).lower():
                    prio = 0 if "apple-touch" in rel.group(1).lower() else 1
                    candidats.append((prio, urljoin(str(page.url), href.group(1))))
        except httpx.HTTPError:
            pass
        urls = [u for _, u in sorted(candidats)] + [urljoin(base, p) for p in ICONES_SECOURS]
        for u in urls:
            try:
                r = c.get(u)
            except httpx.HTTPError:
                continue
            type_ = r.headers.get("content-type", "").split(";")[0]
            if r.status_code == 200 and type_.startswith("image/") and 0 < len(r.content) < 2_000_000:
                return r.content, type_
    return None


@app.get("/api/icone/{pid}")
def api_icone(pid: str):
    if pid not in PORTS or DEMO:
        raise HTTPException(404)
    if pid not in CACHE_ICONES:
        CACHE_ICONES[pid] = chercher_icone(pid)
    trouve = CACHE_ICONES[pid]
    if not trouve:
        CACHE_ICONES.pop(pid, None)
        raise HTTPException(404)
    return Response(content=trouve[0], media_type=trouve[1], headers={"Cache-Control": "max-age=86400"})


@app.post("/api/outils/{pid}/redemarrer")
def api_redemarrer(pid: str, request: Request):
    o = outil(pid)
    return lancer_tache(f"Redémarrage de {o['nom']}", f"cd '{o['dossier']}' && docker compose restart", request.state.nom, pid)


@app.post("/api/outils/{pid}/demarrer")
def api_demarrer(pid: str, request: Request):
    exiger_admin(request)
    o = outil(pid)
    script = f"cd '{o['dossier']}' && docker compose up -d"
    if pid in ADRESSES:
        script += f" && (docker start {ADRESSES[pid]} || true)"
    return lancer_tache(f"Activation de {o['nom']}", script, request.state.nom, pid)


@app.post("/api/outils/{pid}/arreter")
def api_arreter(pid: str, request: Request):
    exiger_admin(request)
    o = outil(pid)
    if pid in SYSTEME:
        raise HTTPException(400, "Cet outil fait partie du système.")
    script = f"cd '{o['dossier']}' && docker compose stop"
    if pid in ADRESSES:
        script += f" && (docker stop {ADRESSES[pid]} || true)"
    return lancer_tache(f"Désactivation de {o['nom']}", script, request.state.nom, pid)


@app.post("/api/outils/{pid}/mettre-a-jour")
def api_maj(pid: str, request: Request):
    exiger_admin(request)
    o = outil(pid)
    script = f"cd '{o['dossier']}' && docker compose pull && docker compose up -d && docker image prune -f"
    return lancer_tache(f"Mise à jour de {o['nom']}", script, request.state.nom, pid)


# ---------------------------------------------------------------- accueil / système
@app.get("/api/moi")
def api_moi(request: Request):
    serveur = "serveur-demo" if DEMO else hote(["hostname"])[1].strip()
    j = request.state.session
    return {"login": request.state.user, "nom": request.state.nom, "version": VERSION, "serveur": serveur,
            "remise": comptes.remise_en_attente(request.state.user),
            "mode": "admin" if j and not j.get("provisoire") else "invite",
            "admin": {"identifiant": j["identifiant"], "rang": j["rang"], "provisoire": j.get("provisoire", False)} if j else None}


@app.get("/api/etat")
def api_etat():
    if DEMO:
        from demo import ETAT
        return ETAT
    reveiller_disques()
    disques = espace(["/", "/srv/data"])
    outils = lister_outils()
    code, _, _ = hote(["test", "-f", "/var/run/reboot-required"])
    soucis = [o["nom"] for o in outils if o["etat"] in ("arrêté", "en panne", "partiel") and o["id"] != "jabba-app"]
    return {
        "cpu": cpu_pourcent(),
        "memoire": memoire(),
        "temperature": temperature(),
        "systeme": disques.get("/"),
        "donnees": disques.get("/srv/data"),
        "duree": duree_marche(),
        "redemarrage_requis": code == 0,
        "outils_actifs": sum(1 for o in outils if o["etat"] == "actif"),
        "outils_total": len(outils),
        "soucis": soucis,
    }


@app.get("/api/systeme")
def api_systeme():
    if DEMO:
        from demo import SYSTEME
        return SYSTEME
    _, nom, _ = hote(["hostname"])
    _, osr, _ = hote(["cat", "/etc/os-release"])
    jolie = re.search(r'PRETTY_NAME="([^"]+)"', osr)
    _, noyau, _ = hote(["uname", "-r"])
    code, _, _ = hote(["test", "-f", "/var/run/reboot-required"])
    _, journal, _ = hote_sh("tail -n 200 /var/log/unattended-upgrades/unattended-upgrades.log 2>/dev/null")
    derniere = None
    for ligne in journal.splitlines():
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d)", ligne)
        if m:
            derniere = m.group(1)
    appareils = []
    c, ts, _ = hote(["tailscale", "status", "--json"], timeout=10)
    if c == 0:
        try:
            d = json.loads(ts)
            for p in (d.get("Peer") or {}).values():
                appareils.append({"nom": p.get("HostName"), "os": p.get("OS"), "en_ligne": p.get("Online", False)})
        except ValueError:
            pass
    return {
        "nom": nom.strip(), "os": jolie.group(1) if jolie else "", "noyau": noyau.strip(),
        "duree": duree_marche(), "redemarrage_requis": code == 0,
        "derniere_maj_auto": derniere, "appareils": sorted(appareils, key=lambda a: (not a["en_ligne"], a["nom"] or "")),
        "version_appli": VERSION, "courant": COURANT, "conso": lire_conso(),
    }


# ---------------------------------------------------------------- terminal du serveur
@app.post("/api/terminal/ticket")
def api_terminal_ticket(request: Request):
    """Réservé au super admin, mot de passe récent. Le ticket ouvre une seule connexion, dans les 30 s."""
    j = exiger_super(request, fort=True)
    if j["identifiant"] != nom_hote():
        c = comptes.trouver(j["identifiant"])
        if c and c.get("terminal") is False:
            raise HTTPException(403, "Le terminal t'a été retiré par un super admin.")
    notifs.envoyer("securite", "Terminal ouvert", f"{j['identifiant']} ({request.state.nom}) a ouvert le terminal du serveur.",
                   "/#/journal", famille="admin")
    return {"ticket": terminal.nouveau_ticket(j["identifiant"], request.state.nom), "compte": admin_user()}


@app.websocket("/api/terminal/ws")
async def ws_terminal(ws: WebSocket, ticket: str = ""):
    hote_page = (ws.headers.get("host") or "").split(":")[0]
    origine = (ws.headers.get("origin") or "").split("//")[-1].split(":")[0]
    qui = terminal.prendre_ticket(ticket)
    if not qui or not origine or origine != hote_page:
        await ws.close(code=4403)
        return
    await ws.accept()
    compte = admin_user()
    sid = secrets.token_hex(4)
    # Chaque commande tapée part au journal du serveur, signée par le compte Jabba
    # (pas seulement par le compte Linux, partagé par tous les super admins).
    env = {"JABBA_QUI": qui["qui"], "JABBA_SESSION": sid}
    if DEMO:
        commande = ["bash", "--rcfile", terminal.preparer_rc(None), "-i"]
    else:
        rc = terminal.preparer_rc(lambda contenu: hote_entree(
            ["sh", "-c", "mkdir -p /usr/local/lib/jabba && cat > /usr/local/lib/jabba/terminal.bashrc"], contenu))
        commande = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--",
                    "runuser", "-u", compte, "--", "bash", "--rcfile", rc, "-i"]
    debut = time.time()
    journal.noter("admin", f"Terminal ouvert par {qui['qui']} (compte {compte} du serveur)", qui["nom"])
    await terminal.relier(ws, commande, demo=DEMO, env=env)
    for cmd in terminal.commandes(sid, debut, None if DEMO else hote):
        journal.noter("admin", f"Terminal · {qui['qui']} : {cmd}", qui["nom"])
    journal.noter("admin", f"Terminal fermé ({qui['qui']}, {max(1, round((time.time() - debut) / 60))} min)", qui["nom"])


@app.post("/api/systeme/redemarrer")
def api_reboot(request: Request):
    exiger_admin(request, fort=True)
    if DEMO:
        return {"ok": True}
    journal.noter("systeme", "Redémarrage du serveur demandé", request.state.nom)
    hote(["systemd-run", "--on-active=3", "systemctl", "reboot"])
    return {"ok": True}


# ---------------------------------------------------------------- disques
@app.get("/api/disques")
def api_disques():
    if DEMO:
        from demo import DISQUES
        return DISQUES
    reveiller_disques()
    code, sortie, err = hote(["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINT,RM,TRAN,TYPE,MODEL"])
    if code != 0:
        raise HTTPException(500, err[:200])
    arbre = json.loads(sortie)["blockdevices"]
    montes = []
    for d in arbre:
        for p in d.get("children") or []:
            if p.get("mountpoint"):
                montes.append(p["mountpoint"])
    usage = espace([m for m in montes if m.startswith("/")]) if montes else {}
    roles = {"/": "Système", "/boot/efi": "Démarrage", "/srv/data": "Données"}
    connus = {chemin: nom for nom, chemin in disques_connus().items()}
    auto = {x["chemin"] for x in disques_auto.lire()["disques"]}
    oublies = set(disques_auto.lire()["oublies"])
    res = []
    for d in arbre:
        if d.get("type") != "disk" or d["name"].startswith(("loop", "zram", "sr")):
            continue
        parts = []
        for p in d.get("children") or []:
            if p.get("fstype") in (None, "swap") and not p.get("mountpoint"):
                continue
            if p.get("mountpoint") and p["mountpoint"].startswith("/boot/") and p["mountpoint"] != "/boot/efi":
                continue
            mp = p.get("mountpoint")
            parts.append({
                "nom": p["name"], "taille": p["size"], "type": p.get("fstype"), "etiquette": p.get("label"),
                "montage": mp, "role": ("Disque externe" if mp in connus else roles.get(mp, "Non utilisé" if not mp else mp)),
                "usage": usage.get(mp),
                "disque_jabba": {"nom": connus[mp], "ecriture": mode_ecriture(mp), "auto": mp in auto} if mp in connus else None,
                "oublie": (p.get("uuid") or "").lower() if (p.get("uuid") or "").lower() in oublies and not mp else None,
                "illisible": disques_auto.ILLISIBLES.get((p.get("fstype") or "").lower()),
            })
        res.append({
            "nom": d["name"], "modele": (d.get("model") or "").strip(), "taille": d["size"],
            "externe": bool(d.get("rm")) or d.get("tran") == "usb", "partitions": parts,
        })
    return res


# ---------------------------------------------------------------- fichiers (Nextcloud)
DAV_NS = {"d": "DAV:", "oc": "http://owncloud.org/ns", "nc": "http://nextcloud.org/ns"}
PROPFIND = """<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
 <d:prop><d:getlastmodified/><d:getcontentlength/><d:resourcetype/><oc:size/><oc:permissions/><d:getcontenttype/><nc:mount-type/></d:prop>
</d:propfind>"""


def nc_pret():
    if DEMO:
        return
    if not (NC_USER and NC_PASS):
        raise HTTPException(503, "Le lien avec Nextcloud n'est pas encore réglé (fichier .env).")


def chemin_propre(chemin: str) -> str:
    morceaux = [m for m in (chemin or "").split("/") if m not in ("", ".")]
    if any(m == ".." for m in morceaux):
        raise HTTPException(400, "Chemin invalide")
    return "/" + "/".join(morceaux)


def dav_url(chemin: str) -> str:
    base = f"/remote.php/dav/files/{quote(NC_USER)}"
    return NC_URL + base + quote(chemin_propre(chemin))


def client():
    entetes = {"Host": NC_HOST} if NC_HOST else {}
    return httpx.AsyncClient(auth=(NC_USER, NC_PASS), headers=entetes, timeout=httpx.Timeout(60, read=600))


def item_dav(rep):
    """Une réponse WebDAV de Nextcloud -> un élément de l'écran Fichiers."""
    prefixe = f"/remote.php/dav/files/{NC_USER}"
    href = unquote(rep.find("d:href", DAV_NS).text)
    rel = "/" + href.split(prefixe, 1)[-1].strip("/")
    prop = rep.find("d:propstat/d:prop", DAV_NS)
    est_dossier = prop.find("d:resourcetype/d:collection", DAV_NS) is not None
    perms = (prop.findtext("oc:permissions", "", DAV_NS) or "")
    taille = prop.findtext("oc:size", None, DAV_NS) or prop.findtext("d:getcontentlength", None, DAV_NS)
    modif = prop.findtext("d:getlastmodified", None, DAV_NS)
    return {
        "nom": rel.rsplit("/", 1)[-1] or "Nextcloud", "chemin": rel, "dossier": est_dossier,
        "taille": int(taille) if taille and taille.lstrip("-").isdigit() and int(taille) >= 0 else None,
        "modifie": parsedate_to_datetime(modif).isoformat() if modif else None,
        "type": prop.findtext("d:getcontenttype", None, DAV_NS),
        "ecriture": any(x in perms for x in ("W", "C", "K")),
        "externe": prop.findtext("nc:mount-type", "", DAV_NS) == "external",
    }


async def lire_dossier(chemin):
    if DEMO:
        from demo import fichiers
        return fichiers(chemin)
    async with client() as c:
        r = await c.request("PROPFIND", dav_url(chemin), content=PROPFIND, headers={"Depth": "1", "Content-Type": "application/xml"})
    if r.status_code == 404:
        raise HTTPException(404, "Dossier introuvable")
    if r.status_code == 401:
        raise HTTPException(502, "Nextcloud refuse le mot de passe d'application (fichier .env).")
    if r.status_code >= 400:
        raise HTTPException(502, f"Nextcloud a répondu {r.status_code}")
    racine = ET.fromstring(r.content)
    elements, dossier = [], None
    for rep in racine.findall("d:response", DAV_NS):
        item = item_dav(rep)
        rel = item["chemin"]
        if rel.rstrip("/") == chemin.rstrip("/") or (rel == "/" and chemin == "/"):
            dossier = item
        else:
            elements.append(item)
    elements.sort(key=lambda e: (not e["dossier"], e["nom"].lower()))
    return {
        "chemin": chemin, "elements": elements,
        "ecriture": dossier["ecriture"] if dossier else True,
        "lien_nextcloud": f"{NC_PUBLIC}/apps/files/?dir={quote(chemin)}" if NC_PUBLIC else None,
    }


@app.get("/api/fichiers")
async def api_fichiers(chemin: str = "/"):
    chemin = chemin_propre(chemin)
    if source() == "disque":
        d = fs(lambda: disque.lister(chemin, *disques_fs()))
    else:
        nc_pret()
        d = await lire_dossier(chemin)
        d["source"] = "nextcloud"
    if chemin != "/":
        noter_visite(chemin)
    d["favori"] = chemin in lire_donnees()["favoris"]
    return d


# ---------------------------------------------------------------- source de l'écran Fichiers
_CACHE_SOURCE = {"t": 0, "v": None}
EJECTES = {}          # nom du disque -> moment de l'éjection
PAUSE_EJECTION = 10 * 60


def source():
    """« nextcloud » si Nextcloud est activé et relié, sinon « disque » (lecture directe du serveur)."""
    if DEMO:
        return os.environ.get("JABBA_DEMO_SOURCE", "nextcloud")
    if not (NC_USER and NC_PASS):
        return "disque"
    if time.time() - _CACHE_SOURCE["t"] < 15:
        return _CACHE_SOURCE["v"]
    code, sortie, _ = hote(["docker", "inspect", "-f", "{{.State.Running}}", "nextcloud"], timeout=10)
    v = "nextcloud" if code == 0 and sortie.strip() == "true" else "disque"
    _CACHE_SOURCE.update(t=time.time(), v=v)
    return v


def ejecte_recemment(nom):
    return time.time() - EJECTES.get(nom, 0) < PAUSE_EJECTION


def reveiller_disques():
    """Un accès réveille l'ouverture automatique des disques branchés (sauf ceux qu'on vient d'éjecter)."""
    if DEMO:
        return
    for nom, chemin in disques_connus().items():
        if re.fullmatch(r"/[\w./-]+", chemin) and not ejecte_recemment(nom) and disque_present(chemin):
            ouvrir_disque(chemin)


_OUVERTS = set()   # disques vus ouverts au dernier passage


def ouvrir_disque(chemin):
    """Ouvre un disque branché. Si l'ouverture automatique n'est pas armée (réglage
    modifié sans redémarrage, disque rebranché…), l'appli la réarme elle-même.
    Quand un disque vient d'être (re)branché, Nextcloud refait son inventaire."""
    ok = _ouvrir(chemin)
    if ok and chemin not in _OUVERTS:
        threading.Thread(target=actualiser_nextcloud, args=(chemin,), daemon=True).start()
    (_OUVERTS.add if ok else _OUVERTS.discard)(chemin)
    return ok


def actualiser_nextcloud(chemin):
    """Nextcloud garde en mémoire le contenu d'un disque, même vide (disque débranché) :
    on lui fait relire le disque et on remet son réglage lecture seule / écriture."""
    occ = ["docker", "exec", "-u", "www-data", "nextcloud", "php", "occ"]
    code, sortie, _ = hote(occ + ["files_external:list", "--output=json"], timeout=60)
    if code != 0:
        return   # Nextcloud arrêté ou absent
    try:
        montages = json.loads(sortie)
    except ValueError:
        return
    code, sortie, _ = hote(occ + ["user:list", "--output=json"], timeout=60)
    try:
        utilisateurs = list(json.loads(sortie)) if code == 0 else []
    except ValueError:
        utilisateurs = []
    dossier = os.path.basename(chemin.rstrip("/"))
    for m in montages:
        if (m.get("configuration") or {}).get("datadir", "").rstrip("/") != f"/mnt/disques/{dossier}":
            continue
        hote(occ + ["files_external:option", str(m["mount_id"]), "readonly",
                    "false" if mode_ecriture(chemin) else "true"], timeout=60)
        point = m.get("mount_point", "").strip("/")
        for u in m.get("applicable_users") or utilisateurs:
            hote(occ + ["files:scan", f"--path=/{u}/files/{point}"], timeout=3600)
    journal.noter("disque", f"Nextcloud a relu {dossier}")


def _ouvrir(chemin):
    hote(["ls", chemin], timeout=10)
    if hote(["mountpoint", "-q", chemin], timeout=10)[0] == 0:
        return True
    _, unite, _ = hote(["systemd-escape", "--path", "--suffix=automount", chemin])
    unite = unite.strip()
    hote(["sh", "-c", f"systemctl daemon-reload; systemctl reset-failed '{unite[:-10]}.mount' 2>/dev/null; "
                      f"systemctl restart '{unite}'; ls '{chemin}' >/dev/null 2>&1"], timeout=30)
    return hote(["mountpoint", "-q", chemin], timeout=10)[0] == 0


def mode_ecriture(chemin):
    """True si le disque monté sur `chemin` est déclaré en écriture dans /etc/fstab."""
    if DEMO:
        return bool(os.environ.get("JABBA_DEMO_ECRITURE"))
    code, fstab, _ = hote(["cat", "/etc/fstab"])
    for ligne in fstab.splitlines():
        champs = ligne.split()
        if len(champs) >= 4 and not ligne.lstrip().startswith("#") and champs[1] == chemin:
            return "ro" not in champs[3].split(",")
    return False


def disques_fs():
    """Pour l'écran Fichiers en mode disque : disques branchés (nom -> chemin) et ceux en lecture seule."""
    branches = disques_branches()
    connus = disques_connus()
    disques = {n: connus[n] for n in branches if n in connus}
    lecture_seule = {n for n, c in disques.items() if not mode_ecriture(c)}
    return disques, lecture_seule


def fs(fonction):
    try:
        return fonction()
    except disque.Refus as e:
        raise HTTPException(e.code, e.message)
    except PermissionError:
        raise HTTPException(403, "Le serveur refuse : droits insuffisants sur ce dossier.")


# ---------------------------------------------------------------- accueil : essentiels, favoris, suggestions
DONNEES = os.environ.get("JABBA_DONNEES", "/data/accueil.json")
VERROU = threading.Lock()
SEUIL_SUGGESTION = 5          # ouvertures…
FENETRE_SUGGESTION = 30 * 86400  # …sur 30 jours


def lire_donnees():
    try:
        with open(DONNEES) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("favoris", [])
    d.setdefault("visites", {})
    d.setdefault("ignores", [])
    return d


def ecrire_donnees(d):
    os.makedirs(os.path.dirname(DONNEES), exist_ok=True)
    tmp = DONNEES + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, DONNEES)


def noter_visite(chemin):
    with VERROU:
        d = lire_donnees()
        limite = time.time() - FENETRE_SUGGESTION
        d["visites"] = {c: [t for t in ts if t > limite] for c, ts in d["visites"].items() if any(t > limite for t in ts)}
        d["visites"].setdefault(chemin, []).append(int(time.time()))
        d["visites"][chemin] = d["visites"][chemin][-50:]
        ecrire_donnees(d)


def disques_env():
    res = {}
    for morceau in os.environ.get("DISQUES", "").split(","):
        if "=" in morceau:
            nom, chemin = morceau.split("=", 1)
            res[nom.strip()] = chemin.strip()
    return res


def disques_connus():
    """Disques externes : « nom -> chemin sur le serveur ». Ceux du .env, puis ceux ajoutés tout seuls."""
    res = disques_env()
    if not DEMO:
        for nom, chemin in disques_auto.connus().items():
            res.setdefault(nom, chemin)
    return res


_CACHE_UUID = {"t": 0, "v": {}}


def uuid_disque(chemin):
    """Identifiant du disque monté sur `chemin`, lu dans /etc/fstab (gardé 1 minute)."""
    if time.time() - _CACHE_UUID["t"] > 60:
        _, fstab, _ = hote(["cat", "/etc/fstab"])
        v = {}
        for ligne in fstab.splitlines():
            champs = ligne.split()
            if len(champs) >= 2 and not ligne.lstrip().startswith("#") and champs[0].upper().startswith("UUID="):
                v[champs[1]] = champs[0].split("=", 1)[1].strip('"')
        _CACHE_UUID.update(t=time.time(), v=v)
    return _CACHE_UUID["v"].get(chemin)


def disque_present(chemin):
    """Branché ? Vérifié sans réveiller l'ouverture automatique (qui attendrait 5 s si débranché)."""
    return disques_auto.present(hote, uuid_disque(chemin))


def disques_branches():
    """Noms des disques externes actuellement branchés."""
    if DEMO:
        return {"R2-D2"}
    branches = set()
    for nom, chemin in disques_connus().items():
        if not re.fullmatch(r"/[\w./-]+", chemin) or ejecte_recemment(nom) or not disque_present(chemin):
            continue
        if ouvrir_disque(chemin):
            branches.add(nom)
    return branches


@app.get("/api/accueil/dossiers")
async def api_accueil_dossiers():
    if source() == "disque":
        racine = fs(lambda: disque.lister("/", *disques_fs()))
    else:
        nc_pret()
        racine = await lire_dossier("/")
    branches, connus = disques_branches(), disques_connus()
    essentiels = [{"nom": "Mes fichiers", "chemin": "/", "type": "racine"}]
    for e in racine["elements"]:
        if source() == "disque" and e["dossier"]:
            essentiels.append({"nom": e["nom"], "chemin": e["chemin"], "type": "disque" if e["externe"] else "espace"})
        elif e["dossier"] and e["externe"]:
            if e["nom"] in connus:
                if e["nom"] in branches:
                    essentiels.append({"nom": e["nom"], "chemin": e["chemin"], "type": "disque"})
            else:
                essentiels.append({"nom": e["nom"], "chemin": e["chemin"], "type": "espace"})
    d = lire_donnees()
    favoris = [{"nom": c.rsplit("/", 1)[-1], "chemin": c} for c in d["favoris"]]
    limite = time.time() - FENETRE_SUGGESTION
    suggestions = []
    for c, ts in d["visites"].items():
        n = sum(1 for t in ts if t > limite)
        if n >= SEUIL_SUGGESTION and c not in d["favoris"] and c not in d["ignores"]:
            suggestions.append({"nom": c.rsplit("/", 1)[-1], "chemin": c, "ouvertures": n})
    suggestions.sort(key=lambda s: -s["ouvertures"])
    return {"essentiels": essentiels, "favoris": favoris, "suggestions": suggestions[:2]}


@app.post("/api/favoris")
async def api_favoris(request: Request):
    corps = await request.json()
    chemin = chemin_propre(corps.get("chemin", ""))
    action = corps.get("action")
    if chemin == "/" or action not in ("ajouter", "retirer", "ignorer"):
        raise HTTPException(400, "Demande invalide")
    with VERROU:
        d = lire_donnees()
        if action == "ajouter" and chemin not in d["favoris"]:
            d["favoris"].append(chemin)
        elif action == "retirer":
            d["favoris"] = [c for c in d["favoris"] if c != chemin]
        elif action == "ignorer" and chemin not in d["ignores"]:
            d["ignores"].append(chemin)
        ecrire_donnees(d)
    return {"ok": True, "favori": chemin in d["favoris"]}


async def est_dossier_nc(chemin):
    async with client() as c:
        r = await c.request("PROPFIND", dav_url(chemin), content=PROPFIND, headers={"Depth": "0", "Content-Type": "application/xml"})
    if r.status_code >= 400:
        return False
    racine = ET.fromstring(r.content)
    return racine.find(".//d:resourcetype/d:collection", DAV_NS) is not None


async def verifier(r, quoi):
    if r.status_code in (403, 405) :
        raise HTTPException(403, f"{quoi} impossible ici (dossier en lecture seule ?).")
    if r.status_code == 409:
        raise HTTPException(409, f"{quoi} impossible : le dossier parent n'existe pas.")
    if r.status_code == 412:
        raise HTTPException(409, f"{quoi} impossible : un élément porte déjà ce nom.")
    if r.status_code >= 400:
        raise HTTPException(502, f"{quoi} : Nextcloud a répondu {r.status_code}")


@app.post("/api/fichiers/dossier")
async def api_mkdir(request: Request):
    corps = await request.json()
    chemin = chemin_propre(corps.get("chemin", ""))
    if source() == "disque":
        fs(lambda: disque.creer_dossier(chemin, *disques_fs()))
        return {"ok": True}
    nc_pret()
    if DEMO:
        return {"ok": True}
    async with client() as c:
        r = await c.request("MKCOL", dav_url(chemin))
    if r.status_code == 405:
        raise HTTPException(409, "Un dossier porte déjà ce nom.")
    await verifier(r, "Création du dossier")
    return {"ok": True}


@app.post("/api/fichiers/envoyer")
async def api_envoyer(request: Request, chemin: str = "/"):
    chemin = chemin_propre(chemin)
    formulaire = await request.form()
    envoyes = []
    if source() == "disque":
        params = disques_fs()
        for f in formulaire.getlist("fichiers"):
            if not isinstance(f, UploadFile) or not f.filename:
                continue
            nom = os.path.basename(f.filename.replace("\\", "/"))
            reel = fs(lambda: disque.chemin_envoi(chemin, nom, *params))
            if os.path.exists(reel):
                raise HTTPException(409, f"« {nom} » existe déjà dans ce dossier.")
            with open(reel, "wb") as sortie:
                while bloc := await f.read(1024 * 1024):
                    sortie.write(bloc)
            disque.fin_envoi(reel)
            envoyes.append(nom)
        noter_envoi(request, chemin, envoyes)
        return {"envoyes": envoyes}
    nc_pret()
    for f in formulaire.getlist("fichiers"):
        if not isinstance(f, UploadFile) or not f.filename:
            continue
        nom = os.path.basename(f.filename.replace("\\", "/"))
        if DEMO:
            envoyes.append(nom)
            continue

        async def morceaux(fichier=f):
            while bloc := await fichier.read(1024 * 1024):
                yield bloc

        async with client() as c:
            r = await c.put(dav_url(f"{chemin}/{nom}"), content=morceaux())
        await verifier(r, f"Envoi de {nom}")
        envoyes.append(nom)
    noter_envoi(request, chemin, envoyes)
    return {"envoyes": envoyes}


def noter_envoi(request, chemin, envoyes):
    if envoyes:
        quoi = f"« {envoyes[0]} »" if len(envoyes) == 1 else f"{len(envoyes)} fichiers"
        journal.noter("fichier", f"{quoi} envoyé{'s' if len(envoyes) > 1 else ''} dans {chemin}", request.state.nom)


@app.get("/api/fichiers/telecharger")
async def api_telecharger(chemin: str):
    chemin = chemin_propre(chemin)
    if source() == "disque":
        reel = fs(lambda: disque.chemin_lecture(chemin, *disques_fs()))
        return FileResponse(reel, filename=os.path.basename(reel))
    nc_pret()
    if DEMO:
        raise HTTPException(404, "Pas de fichiers en démo")
    c = client()
    req = c.build_request("GET", dav_url(chemin))
    r = await c.send(req, stream=True)
    if r.status_code >= 400:
        await r.aclose()
        await c.aclose()
        raise HTTPException(r.status_code, "Fichier introuvable")
    nom = chemin.rsplit("/", 1)[-1]

    async def flux():
        try:
            async for bloc in r.aiter_bytes():
                yield bloc
        finally:
            await r.aclose()
            await c.aclose()

    entetes = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nom)}"}
    if "content-length" in r.headers:
        entetes["Content-Length"] = r.headers["content-length"]
    return StreamingResponse(flux(), media_type=r.headers.get("content-type", "application/octet-stream"), headers=entetes)


@app.post("/api/fichiers/deplacer")
async def api_deplacer(request: Request):
    corps = await request.json()
    de, vers = chemin_propre(corps.get("de", "")), chemin_propre(corps.get("vers", ""))
    if de == "/" or vers == "/":
        raise HTTPException(400, "Chemin invalide")
    if source() == "disque":
        fs(lambda: disque.deplacer(de, vers, *disques_fs()))
        return {"ok": True}
    nc_pret()
    if DEMO:
        return {"ok": True}
    async with client() as c:
        r = await c.request("MOVE", dav_url(de), headers={"Destination": dav_url(vers), "Overwrite": "F"})
    await verifier(r, "Renommage")
    return {"ok": True}


@app.post("/api/fichiers/supprimer")
async def api_supprimer(request: Request):
    corps = await request.json()
    chemin = chemin_propre(corps.get("chemin", ""))
    if chemin == "/" or len([m for m in chemin.split("/") if m]) < 2 and source() == "disque":
        raise HTTPException(400, "Ce dossier ne peut pas être supprimé.")
    if source() == "disque":
        params = disques_fs()
        if fs(lambda: disque.est_dossier(chemin, *params)):
            exiger_admin(request)  # supprimer un dossier entier demande le mot de passe
        fs(lambda: disque.supprimer(chemin, *params))
        journal.noter("fichier", f"{chemin} mis à la corbeille", request.state.nom)
        return {"ok": True}
    nc_pret()
    if DEMO:
        if corps.get("dossier"):
            exiger_admin(request)
        return {"ok": True}
    if await est_dossier_nc(chemin):
        exiger_admin(request)
    async with client() as c:
        r = await c.delete(dav_url(chemin))
    await verifier(r, "Suppression")
    journal.noter("fichier", f"{chemin} mis à la corbeille", request.state.nom)
    return {"ok": True}


# ---------------------------------------------------------------- disques : mode d'accès et éjection
def chemin_disque(nom):
    chemin = disques_connus().get(nom)
    if not chemin or not re.fullmatch(r"/[\w./-]+", chemin):
        raise HTTPException(404, "Disque inconnu")
    return chemin


@app.post("/api/disques/{nom}/mode")
async def api_disque_mode(nom: str, request: Request):
    exiger_admin(request)
    chemin = chemin_disque(nom)
    ecriture = bool((await request.json()).get("ecriture"))
    if DEMO:
        return {"ok": True, "ecriture": ecriture}
    code, fstab, _ = hote(["cat", "/etc/fstab"])
    lignes, trouve = [], False
    for ligne in fstab.splitlines():
        champs = ligne.split()
        if len(champs) >= 4 and not ligne.lstrip().startswith("#") and champs[1] == chemin:
            options = [o for o in champs[3].split(",") if o not in ("ro", "rw")]
            champs[3] = ",".join(["rw" if ecriture else "ro"] + options)
            ligne = " ".join(champs)
            trouve = True
        lignes.append(ligne)
    if not trouve:
        raise HTTPException(404, "Ce disque n'est pas déclaré sur le serveur.")
    code, _, err = hote_entree(["sh", "-c", "cp /etc/fstab /etc/fstab.jabba-bak && cat > /etc/fstab"], "\n".join(lignes) + "\n")
    if code != 0:
        raise HTTPException(500, "Impossible d'enregistrer le réglage : " + err[:200])
    _, unite, _ = hote(["systemd-escape", "--path", "--suffix=automount", chemin])
    hote_sh(f"systemctl daemon-reload; umount '{chemin}' 2>/dev/null; systemctl restart '{unite.strip()}'; ls '{chemin}' >/dev/null 2>&1", timeout=30)
    # Même réglage côté Nextcloud, s'il est activé.
    code, sortie, _ = hote(["docker", "exec", "-u", "www-data", "nextcloud", "php", "occ", "files_external:list", "--output=json"], timeout=60)
    if code == 0:
        try:
            for m in json.loads(sortie):
                if m.get("mount_point", "").strip("/") == nom:
                    hote(["docker", "exec", "-u", "www-data", "nextcloud", "php", "occ", "files_external:option",
                          str(m["mount_id"]), "readonly", "false" if ecriture else "true"], timeout=60)
        except ValueError:
            pass
    journal.noter("disque", f"{nom} : {'écriture autorisée' if ecriture else 'lecture seule'}", request.state.nom)
    return {"ok": True, "ecriture": ecriture}


@app.post("/api/disques/{nom}/ejecter")
def api_disque_ejecter(nom: str, request: Request):
    chemin = chemin_disque(nom)
    if DEMO:
        EJECTES[nom] = time.time()
        return {"ok": True}
    code, _, err = hote_sh(f"sync && umount '{chemin}'", timeout=120)
    if code != 0 and "not mounted" not in err:
        raise HTTPException(409, "Le disque est en cours d'utilisation (lecture d'un film, copie…). Arrête ce qui l'utilise et réessaie.")
    EJECTES[nom] = time.time()
    journal.noter("disque", f"{nom} éjecté", request.state.nom)
    return {"ok": True}


@app.post("/api/disques/{nom}/oublier")
def api_disque_oublier(nom: str, request: Request):
    exiger_admin(request, fort=True)
    if DEMO:
        return {"ok": True}
    try:
        x = disques_auto.oublier(hote, hote_entree, nom)
    except KeyError:
        raise HTTPException(404, "Seuls les disques ajoutés automatiquement peuvent être oubliés ici.")
    journal.noter("disque", f"{x['nom']} oublié : il ne sera plus ajouté tout seul", request.state.nom)
    return {"ok": True}


@app.post("/api/disques/reprendre")
async def api_disque_reprendre(request: Request):
    exiger_admin(request)
    uuid_ = str((await request.json()).get("uuid", ""))
    if not re.fullmatch(r"[\w-]{4,64}", uuid_):
        raise HTTPException(400, "Disque invalide")
    if not DEMO:
        disques_auto.reprendre(uuid_)
        threading.Thread(target=scruter_disques, daemon=True).start()
    journal.noter("disque", "Disque remis en ajout automatique", request.state.nom)
    return {"ok": True}


# ---------------------------------------------------------------- aperçus et visionneuse
TYPES_VISIBLES = ("image/", "video/", "audio/", "application/pdf", "text/plain")
EXT_TEXTE = {".txt", ".md", ".csv", ".log", ".json", ".yaml", ".yml", ".ini", ".conf", ".srt", ".nfo", ".py", ".sh"}
mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("video/x-matroska", ".mkv")


def type_visible(nom):
    """Type à donner au navigateur pour l'afficher, ou None (fichier à télécharger)."""
    ext = os.path.splitext(nom)[1].lower()
    if ext in EXT_TEXTE:
        return "text/plain; charset=utf-8"
    t = mimetypes.guess_type(nom)[0] or ""
    return t if t.startswith(TYPES_VISIBLES) else None


def entetes_visionneuse(type_, nom):
    e = {"Content-Disposition": f"inline; filename*=UTF-8''{quote(nom)}", "X-Frame-Options": "SAMEORIGIN",
         "Cache-Control": "private, max-age=300"}
    if type_ != "application/pdf":
        # Une image SVG ou un texte ouvert seul ne peut lancer aucun script.
        e["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"
    return e


@app.get("/api/fichiers/apercu")
async def api_apercu(chemin: str, taille: int = 128):
    chemin = chemin_propre(chemin)
    taille = 256 if taille > 128 else 128
    if DEMO and source() != "disque":
        raise HTTPException(404)
    if source() == "disque":
        reel = fs(lambda: disque.chemin_lecture(chemin, *disques_fs()))
        try:
            cache = await asyncio.to_thread(disque.vignette, reel, taille)
        except disque.Refus as e:
            raise HTTPException(e.code, e.message)
        except Exception:  # noqa: BLE001 — image abîmée ou format inconnu
            raise HTTPException(404, "Pas d'aperçu")
        return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
    nc_pret()
    async with client() as c:
        r = await c.get(NC_URL + "/index.php/core/preview.png",
                        params={"file": chemin, "x": taille, "y": taille, "a": "1", "mode": "cover"})
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
        raise HTTPException(404, "Pas d'aperçu")
    return Response(r.content, media_type=r.headers["content-type"], headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/fichiers/voir")
async def api_voir(chemin: str, request: Request):
    """Le fichier lui-même, à afficher dans l'appli (photos, PDF, vidéos, musique, textes)."""
    chemin = chemin_propre(chemin)
    nom = chemin.rsplit("/", 1)[-1]
    type_ = type_visible(nom)
    if not type_:
        raise HTTPException(415, "Ce type de fichier ne s'affiche pas dans l'appli : télécharge-le.")
    if DEMO and source() != "disque":
        raise HTTPException(404, "Pas de fichiers en démo")
    if source() == "disque":
        reel = fs(lambda: disque.chemin_lecture(chemin, *disques_fs()))
        return FileResponse(reel, media_type=type_, headers=entetes_visionneuse(type_, nom))
    nc_pret()
    c = client()
    entetes = {"Range": request.headers["range"]} if "range" in request.headers else {}
    r = await c.send(c.build_request("GET", dav_url(chemin), headers=entetes), stream=True)
    if r.status_code >= 400:
        await r.aclose()
        await c.aclose()
        raise HTTPException(404 if r.status_code == 404 else 502, "Fichier introuvable")

    async def flux():
        try:
            async for bloc in r.aiter_bytes():
                yield bloc
        finally:
            await r.aclose()
            await c.aclose()

    sortie = entetes_visionneuse(type_, nom)
    for h in ("content-length", "content-range", "accept-ranges", "etag", "last-modified"):
        if h in r.headers:
            sortie[h] = r.headers[h]
    sortie.setdefault("accept-ranges", "bytes")
    return StreamingResponse(flux(), status_code=r.status_code, media_type=type_, headers=sortie)


# ---------------------------------------------------------------- lecteur vidéo (conversion à la volée)
def source_lecture(chemin):
    """Où ffmpeg lit le fichier : sur le disque si possible, sinon par Nextcloud."""
    chemin = chemin_propre(chemin)
    if source() == "disque":
        return {"entree": fs(lambda: disque.chemin_lecture(chemin, *disques_fs()))}
    premier, _, reste = chemin.strip("/").partition("/")
    connus = disques_connus()
    if premier in connus:   # un disque externe : lu directement, sans passer par Nextcloud
        base = os.path.realpath(connus[premier])
        reel = os.path.realpath(os.path.join(base, reste))
        if (reel == base or reel.startswith(base + os.sep)) and os.path.isfile(reel):
            return {"entree": reel}
    nc_pret()
    jeton = base64.b64encode(f"{NC_USER}:{NC_PASS}".encode()).decode()
    return {"entree": dav_url(chemin), "entetes": f"Authorization: Basic {jeton}\r\n"}


_INFOS = {}   # chemin -> (moment, infos) : l'analyse n'est refaite qu'une fois par film (10 min)


def infos_lecture(chemin):
    deja = _INFOS.get(chemin)
    if deja and time.time() - deja[0] < 600:
        return dict(deja[1])
    try:
        infos = lecture.sonder(source_lecture(chemin))
    except RuntimeError as e:
        raise HTTPException(415, str(e))
    except FileNotFoundError:
        raise HTTPException(500, "Le moteur vidéo (ffmpeg) manque dans l'appli.")
    _INFOS[chemin] = (time.time(), infos)
    return dict(infos)


# ---------------------------------------------------------------- Jellyfin : ouvrir un film depuis Fichiers
JELLYFIN = {"t": 0, "index": {}}
FICHIER_JELLYFIN = os.path.join(os.environ.get("JABBA_DATA", "/data"), "jellyfin.json")
FICHIER_CONSO = os.path.join(os.environ.get("JABBA_DATA", "/data"), "consommation.json")


def cle_jellyfin():
    try:
        with open(FICHIER_JELLYFIN) as f:
            return json.load(f).get("cle") or ""
    except (OSError, ValueError):
        return ""


def index_jellyfin(cle):
    """Nom de fichier -> identifiant Jellyfin, pour tous les films et épisodes (gardé 5 minutes)."""
    if time.time() - JELLYFIN["t"] < 300 and JELLYFIN["index"]:
        return JELLYFIN["index"]
    r = httpx.get(f"http://127.0.0.1:{PORTS['jellyfin']}/Items", timeout=30, headers={"X-Emby-Token": cle},
                  params={"Recursive": "true", "IncludeItemTypes": "Movie,Episode,Video", "Fields": "Path",
                          "EnableImages": "false", "EnableUserData": "false"})
    if r.status_code in (401, 403):
        raise HTTPException(409, {"cle_jellyfin": True, "message": "La clé Jellyfin n'est plus valable."})
    r.raise_for_status()
    JELLYFIN["index"] = {os.path.basename(x.get("Path") or ""): x["Id"] for x in r.json().get("Items", []) if x.get("Path")}
    JELLYFIN["t"] = time.time()
    return JELLYFIN["index"]


@app.get("/api/jellyfin/trouver")
def api_jellyfin_trouver(chemin: str):
    lien = LIENS.get("jellyfin")
    if not lien:
        raise HTTPException(404, "Jellyfin n'a pas d'adresse dans les réglages de l'appli (LIENS).")
    cle = cle_jellyfin()
    if not cle:
        raise HTTPException(409, {"cle_jellyfin": True, "message": "Jabba n'est pas encore relié à Jellyfin."})
    try:
        ident = index_jellyfin(cle).get(chemin.rsplit("/", 1)[-1])
    except httpx.HTTPError:
        raise HTTPException(502, "Jellyfin ne répond pas : vérifie qu'il est activé dans l'Établi.")
    if not ident:
        raise HTTPException(404, "Ce fichier n'est pas dans une médiathèque Jellyfin (ou Jellyfin ne l'a pas encore analysé).")
    return {"lien": f"{lien.rstrip('/')}/web/#/details?id={ident}"}


@app.post("/api/jellyfin/cle")
async def api_jellyfin_cle(request: Request):
    j = exiger_admin(request, fort=True)
    cle = str((await request.json()).get("cle", "")).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", cle):
        raise HTTPException(400, "Une clé Jellyfin fait 32 caractères (chiffres et lettres de a à f).")
    try:
        JELLYFIN["t"] = 0
        index_jellyfin(cle)
    except HTTPException:
        raise HTTPException(400, "Jellyfin refuse cette clé.")
    except httpx.HTTPError:
        raise HTTPException(502, "Jellyfin ne répond pas : vérifie qu'il est activé dans l'Établi.")
    with open(FICHIER_JELLYFIN, "w") as f:
        json.dump({"cle": cle}, f)
    os.chmod(FICHIER_JELLYFIN, 0o600)
    journal.noter("admin", f"Jabba relié à Jellyfin, par {j['identifiant']}", request.state.nom)
    return {"ok": True}


@app.get("/api/lecture/infos")
def api_lecture_infos(chemin: str):
    infos = infos_lecture(chemin)
    infos["directe"] = lecture.lecture_directe(infos)
    return infos


@app.post("/api/lecture/demarrer")
async def api_lecture_demarrer(request: Request):
    corps = await request.json()
    chemin = corps.get("chemin", "")
    infos = await asyncio.to_thread(infos_lecture, chemin)
    if not infos["video"]:
        raise HTTPException(415, "Pas d'image dans ce fichier.")
    if corps.get("remplace"):
        lecture.arreter(str(corps["remplace"]))
    sid, debut = await asyncio.to_thread(lecture.demarrer, source_lecture(chemin), infos,
                                         float(corps.get("debut") or 0), corps.get("audio"))
    return {"id": sid, "url": f"/api/lecture/{sid}/index.m3u8", "duree": infos["duree"], "debut": debut}


@app.get("/api/lecture/soustitres")
def api_lecture_soustitres(chemin: str, piste: int, debut: float = 0):
    try:
        vtt = lecture.soustitres(source_lecture(chemin), piste, debut)
    except (RuntimeError, subprocess.TimeoutExpired):
        raise HTTPException(415, "Sous-titres illisibles.")
    return Response(vtt, media_type="text/vtt; charset=utf-8")


@app.get("/api/lecture/{sid}/{nom}")
def api_lecture_morceau(sid: str, nom: str):
    chemin = lecture.fichier(sid, nom)
    if not chemin:
        raise HTTPException(404 if lecture.erreur(sid) is None else 500, lecture.erreur(sid) or "Pas encore prêt.")
    if nom.endswith(".m3u8"):
        # Lecture depuis le début de la liste (et pas « en direct », au dernier morceau produit)
        texte = open(chemin).read().replace("#EXTM3U\n", "#EXTM3U\n#EXT-X-START:TIME-OFFSET=0,PRECISE=YES\n", 1)
        return Response(texte, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})
    return FileResponse(chemin, media_type="video/mp2t", headers={"Cache-Control": "no-store"})


@app.post("/api/lecture/{sid}/arreter")
def api_lecture_arreter(sid: str):
    lecture.arreter(sid)
    return {"ok": True}


# ---------------------------------------------------------------- recherche
SEARCH = """<?xml version="1.0" encoding="UTF-8"?>
<d:searchrequest xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
 <d:basicsearch>
  <d:select><d:prop><d:getlastmodified/><d:getcontentlength/><d:resourcetype/><oc:size/><oc:permissions/><d:getcontenttype/><nc:mount-type/></d:prop></d:select>
  <d:from><d:scope><d:href>/files/{user}</d:href><d:depth>infinity</d:depth></d:scope></d:from>
  <d:where><d:like><d:prop><d:displayname/></d:prop><d:literal>%{texte}%</d:literal></d:like></d:where>
  <d:orderby/>
  <d:limit><d:nresults>60</d:nresults></d:limit>
 </d:basicsearch>
</d:searchrequest>"""


@app.get("/api/recherche")
async def api_recherche(q: str = ""):
    """Cherche par nom, partout : dans Nextcloud et directement sur les disques du serveur."""
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(400, "Tape au moins 2 lettres.")
    if DEMO and source() != "disque":
        from demo import recherche
        return recherche(q)
    disques, lecture_seule = disques_fs()
    if source() == "disque":
        res, complet = await asyncio.to_thread(disque.rechercher, q, disques, lecture_seule)
        return {"elements": res, "complet": complet}
    nc_pret()
    async with client() as c:
        r = await c.request("SEARCH", NC_URL + "/remote.php/dav/", headers={"Content-Type": "text/xml"},
                            content=SEARCH.format(user=xml_escape(NC_USER), texte=xml_escape(q)).encode())
    res = []
    if r.status_code < 400:
        for rep in ET.fromstring(r.content).findall("d:response", DAV_NS):
            if rep.find("d:propstat/d:prop", DAV_NS) is not None:
                res.append(item_dav(rep))
    # Les dossiers du serveur vus dans Nextcloud (films, disques…) ne sont pas
    # tous indexés par Nextcloud : on les parcourt directement.
    racine = await lire_dossier("/")
    externes = {e["nom"] for e in racine["elements"] if e["externe"]}
    direct, complet = await asyncio.to_thread(disque.rechercher, q, disques, lecture_seule, externes)
    vus = {e["chemin"] for e in res}
    res += [e for e in direct if e["chemin"] not in vus]
    res.sort(key=lambda e: (not e["nom"].lower().startswith(q.lower()), not e["dossier"], e["nom"].lower()))
    return {"elements": res[:100], "complet": complet and len(res) <= 100}


# ---------------------------------------------------------------- corbeille
TRASH_PROPS = """<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
 <d:prop><nc:trashbin-filename/><nc:trashbin-original-location/><nc:trashbin-deletion-time/><d:resourcetype/><oc:size/><d:getcontentlength/></d:prop>
</d:propfind>"""


def url_corbeille(ident=""):
    base = f"/remote.php/dav/trashbin/{quote(NC_USER)}/trash"
    return NC_URL + base + ("/" + quote(ident, safe="") if ident else "")


async def corbeille_nc():
    async with client() as c:
        r = await c.request("PROPFIND", url_corbeille(), content=TRASH_PROPS, headers={"Depth": "1", "Content-Type": "application/xml"})
    if r.status_code >= 400:
        return []
    res = []
    for rep in ET.fromstring(r.content).findall("d:response", DAV_NS):
        href = unquote(rep.find("d:href", DAV_NS).text).rstrip("/")
        ident = href.rsplit("/", 1)[-1]
        if ident == "trash":
            continue
        prop = rep.find("d:propstat/d:prop", DAV_NS)
        taille = prop.findtext("oc:size", None, DAV_NS) or prop.findtext("d:getcontentlength", None, DAV_NS)
        quand_ = prop.findtext("nc:trashbin-deletion-time", "0", DAV_NS)
        res.append({
            "id": ident, "nom": prop.findtext("nc:trashbin-filename", ident, DAV_NS),
            "origine": "/" + (prop.findtext("nc:trashbin-original-location", "", DAV_NS) or "").strip("/"),
            "supprime": int(quand_) if quand_.isdigit() else 0,
            "dossier": prop.find("d:resourcetype/d:collection", DAV_NS) is not None,
            "taille": int(taille) if taille and taille.isdigit() else None, "source": "nextcloud",
        })
    return res


@app.get("/api/corbeille")
async def api_corbeille():
    if DEMO and source() != "disque":
        from demo import CORBEILLE
        return {"elements": CORBEILLE, "garde": 30}
    elements = await asyncio.to_thread(disque.lister_corbeille)
    if source() == "nextcloud":
        nc_pret()
        elements = await corbeille_nc() + elements
    elements.sort(key=lambda e: -e["supprime"])
    return {"elements": elements, "garde": 30}


@app.post("/api/corbeille/restaurer")
async def api_corbeille_restaurer(request: Request):
    corps = await request.json()
    ident, src = str(corps.get("id", "")), corps.get("source")
    if DEMO and src != "disque":
        return {"ok": True, "chemin": "/Documents"}
    if src == "disque":
        chemin = fs(lambda: disque.restaurer(ident, *disques_fs()))
    else:
        nc_pret()
        if "/" in ident:
            raise HTTPException(400, "Élément invalide")
        async with client() as c:
            r = await c.request("MOVE", url_corbeille(ident),
                                headers={"Destination": NC_URL + f"/remote.php/dav/trashbin/{quote(NC_USER)}/restore/{quote(ident, safe='')}"})
        await verifier(r, "Restauration")
        chemin = corps.get("origine") or "/"
    journal.noter("fichier", f"{chemin} restauré depuis la corbeille", request.state.nom)
    return {"ok": True, "chemin": chemin}


@app.post("/api/corbeille/effacer")
async def api_corbeille_effacer(request: Request):
    """Effacer pour de bon : un élément, ou toute la corbeille. Demande le mot de passe du serveur."""
    exiger_admin(request, fort=True)
    corps = await request.json()
    ident, src, tout = str(corps.get("id", "")), corps.get("source"), bool(corps.get("tout"))
    if DEMO and source() != "disque":
        return {"ok": True}
    if tout:
        await asyncio.to_thread(disque.vider)
        if source() == "nextcloud" and NC_USER:
            async with client() as c:
                await c.delete(url_corbeille())
        journal.noter("fichier", "Corbeille vidée", request.state.nom)
        return {"ok": True}
    if src == "disque":
        fs(lambda: disque.effacer(ident))
    else:
        nc_pret()
        if "/" in ident:
            raise HTTPException(400, "Élément invalide")
        async with client() as c:
            r = await c.delete(url_corbeille(ident))
        await verifier(r, "Effacement")
    journal.noter("fichier", f"« {corps.get('nom', ident)} » effacé définitivement", request.state.nom)
    return {"ok": True}


# ---------------------------------------------------------------- journal d'activité
@app.get("/api/journal")
def api_journal(n: int = 100, famille: str = ""):
    res = journal.lire(min(max(n, 1), 500), famille or None)
    if DEMO:
        from demo import JOURNAL
        res += [e for e in JOURNAL if not famille or e["famille"] == famille]
    return res


# ---------------------------------------------------------------- alertes (notifications)
@app.get("/api/alertes")
def api_alertes():
    e = notifs.etat()
    e.pop("endpoints")
    return e


@app.post("/api/alertes/abonnement")
async def api_alertes_abonner(request: Request):
    corps = await request.json()
    origine = request.headers.get("origin") or ""
    contact = origine if origine.startswith("https://") else "mailto:jabba@example.org"
    try:
        notifs.abonner(corps.get("abonnement"), contact, str(corps.get("appareil", "")), request.state.user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    journal.noter("alerte", "Alertes activées sur un appareil", request.state.nom)
    return {"ok": True}


@app.post("/api/alertes/abonnement/retirer")
async def api_alertes_desabonner(request: Request):
    notifs.desabonner(str((await request.json()).get("endpoint", "")))
    return {"ok": True}


@app.post("/api/alertes/abonnement/etat")
async def api_alertes_cet_appareil(request: Request):
    endpoint = str((await request.json()).get("endpoint", ""))
    return {"abonne": endpoint in notifs.etat()["endpoints"]}


@app.post("/api/alertes/types")
async def api_alertes_types(request: Request):
    corps = await request.json()
    try:
        notifs.regler(corps.get("type"), corps.get("actif"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/alertes/essai")
def api_alertes_essai(request: Request):
    n = notifs.envoyer("essai", "Alerte d'essai", f"Tout marche : {nom_serveur()} peut te prévenir.", "/#/systeme", forcer=True)
    return {"ok": True, "appareils": n}


# ---------------------------------------------------------------- mise à jour de l'appli
def dossier_appli():
    code, sortie, _ = hote(["docker", "inspect", "-f", '{{index .Config.Labels "com.docker.compose.project.working_dir"}}', "jabba-app"])
    d = sortie.strip()
    return d if code == 0 and re.fullmatch(r"/[\w./-]+", d) else None


@app.get("/api/appli/maj")
def api_appli_maj(verifier: int = 0):
    if DEMO:
        return {"actuelle": VERSION, "depot": "exemple/jabba", "pret": True,
                "derniere": {"version": "0.7.0", "tag": "v0.7.0", "notes": "- Correctifs\n- Aperçus plus rapides", "date": "2026-11-20T10:00:00Z"},
                "disponible": True}
    d = dossier_appli()
    pret = bool(DEPOT and d and hote(["test", "-d", d + "/.git"])[0] == 0 and hote(["sh", "-c", "command -v git"])[0] == 0)
    der = maj.derniere(DEPOT, forcer=bool(verifier)) if DEPOT else None
    return {"actuelle": VERSION, "depot": DEPOT or None, "pret": pret, "derniere": der,
            "disponible": bool(der and maj.plus_recente(VERSION, der["version"]))}


@app.get("/api/appli/versions")
def api_appli_versions():
    """Historique des versions de l'appli : ce qui a changé à chaque mise à jour."""
    try:
        with open(os.path.join(os.path.dirname(__file__), "versions.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


@app.post("/api/appli/maj")
def api_appli_installer(request: Request):
    exiger_admin(request, fort=True)
    if DEMO:
        return {"ok": True}
    der = maj.derniere(DEPOT, forcer=True)
    d = dossier_appli()
    if not (der and d and maj.plus_recente(VERSION, der["version"])):
        raise HTTPException(409, "Pas de nouvelle version à installer.")
    script = maj.script(d, der["tag"])
    journal.noter("appli", f"Mise à jour de l'appli vers {der['version']} lancée", request.state.nom)
    # Lancée par le serveur lui-même : elle continue pendant que l'appli redémarre.
    unite = f"jabba-maj-{int(time.time())}"
    code, _, err = hote(["systemd-run", f"--unit={unite}", "--collect", "sh", "-c", script])
    if code != 0:
        raise HTTPException(500, "Lancement impossible : " + err[:200])
    MAJ.update(unite=unite, version=der["version"], debut=time.time(), signale=False)
    return {"ok": True, "version": der["version"]}


MAJ = {"unite": None, "version": None, "debut": 0, "signale": False}


def etat_maj():
    """Où en est la dernière installation lancée : en_cours, echec (avec le détail) ou termine."""
    if not MAJ["unite"]:
        return {"statut": "aucune"}
    u = MAJ["unite"] + ".service"
    _, actif, _ = hote(["systemctl", "show", "-p", "ActiveState", "--value", u])
    _, log, _ = hote(["journalctl", "-u", u, "--no-pager", "-o", "cat", "-n", "300"])
    lignes = [l for l in log.splitlines() if l.strip()]
    if actif.strip() in ("active", "activating", "reloading"):
        return {"statut": "en_cours", "version": MAJ["version"], "depuis": int(time.time() - MAJ["debut"]), **avancement_maj(lignes)}
    if any("Failed with result" in l or "status=" in l and "status=0/" not in l for l in lignes):
        detail = [l for l in lignes if not l.startswith(("Started ", "jabba-maj-")) and "pam_unix" not in l][-6:]
        if not MAJ["signale"]:
            MAJ["signale"] = True
            notifs.envoyer("mises_a_jour", "Mise à jour échouée",
                           f"La version {MAJ['version']} ne s'est pas installée. Détails dans Système.", "/#/systeme", famille="appli")
        return {"statut": "echec", "version": MAJ["version"], "detail": detail}
    return {"statut": "termine", "version": MAJ["version"]}


def avancement_maj(lignes):
    """Étape et pourcentage, lus dans ce que la mise à jour écrit au fur et à mesure."""
    texte = "\n".join(lignes)
    if re.search(r"Container \S+\s+(Recreate|Starting|Started|Running)", texte):
        return {"etape": "Redémarrage de l'appli", "pourcent": 92}
    etapes = re.findall(r"\[(?:[\w-]+ )?(\d+)/(\d+)\]", texte)
    if etapes:
        x, y = (int(v) for v in etapes[-1])
        return {"etape": f"Construction de l'appli (étape {x} sur {y})", "pourcent": 20 + round(65 * x / max(y, 1))}
    if re.search(r"Building|building|load build definition|resolve image config", texte):
        return {"etape": "Construction de l'appli", "pourcent": 20}
    return {"etape": "Téléchargement de la nouvelle version", "pourcent": 8}


@app.get("/api/appli/maj/etat")
def api_appli_maj_etat(request: Request):
    exiger_admin(request)
    if DEMO:
        if os.environ.get("JABBA_DEMO_MAJ", "en_cours") == "en_cours":
            t = int(time.time()) % 60
            return {"statut": "en_cours", "version": "0.7.0", "depuis": t,
                    **avancement_maj([f"#9 [jabba {1 + t // 10}/6] RUN pip install"] if t > 5 else [])}
        return {"statut": os.environ.get("JABBA_DEMO_MAJ"), "version": "0.7.0",
                "detail": ["erreur : droits insuffisants pour ajouter un objet à la base de données .git/objects du dépôt", "fatal : failed to write object"]}
    return etat_maj()


# ---------------------------------------------------------------- invités : réglages (admin)
def adresse_wifi():
    """Interface et adresse IP du serveur sur le réseau local (celles de la route par défaut)."""
    if DEMO:
        return "eth0", "192.168.1.20"
    _, sortie, _ = hote(["ip", "-o", "-4", "route", "get", "1.1.1.1"])
    m = re.search(r"dev (\S+) .*src (\S+)", sortie)
    return (m.group(1), m.group(2)) if m else (None, None)


def synchroniser_outils_invites():
    """Copie, pour la page invités, la liste des outils et leur état (la porte du Wi-Fi ne parle pas à Docker)."""
    outils = []
    for o in lister_outils():
        if o["id"] in PORTS:
            outils.append({"id": o["id"], "nom": o["nom"], "role": o["role"], "port": PORTS[o["id"]],
                           "actif": o["etat"] != "arrêté", "lien_tailscale": o.get("lien")})
    _, ip = adresse_wifi()

    def f(d):
        d["outils"], d["adresse"] = outils, ip
    invites.modifier(f)


CHAINE = "JABBA-WIFI"


def regles_voulues(interface, d):
    """Une règle par outil fermé au Wi-Fi : bloque son port pour ce qui arrive par la carte réseau du Wi-Fi."""
    regles = []
    for pid, port in sorted(PORTS.items()):
        if not d["wifi"].get(pid):
            regles.append(f"-A {CHAINE} -i {interface} -p tcp -m conntrack --ctorigdstport {port} --ctdir ORIGINAL -j DROP")
    return regles


def appliquer_pare_feu():
    """Les outils non cochés « Sur le Wi-Fi » ne sont plus joignables depuis le réseau local (Tailscale n'est pas touché)."""
    if DEMO:
        return True
    interface, _ = adresse_wifi()
    if not interface or not re.fullmatch(r"[\w.-]+", interface):
        return False
    voulues = regles_voulues(interface, invites.lire())
    ok = True
    for outil_ipt in ("iptables", "ip6tables"):
        if hote([outil_ipt, "-S", "DOCKER-USER"])[0] != 0:
            continue
        code, actuelles, _ = hote([outil_ipt, "-S", CHAINE])
        if code == 0 and [l for l in actuelles.splitlines() if l.startswith("-A ")] == voulues and \
                hote([outil_ipt, "-C", "DOCKER-USER", "-j", CHAINE])[0] == 0:
            continue
        script = f"{outil_ipt} -N {CHAINE} 2>/dev/null; {outil_ipt} -F {CHAINE}"
        for r in voulues:
            script += f" && {outil_ipt} {r}"
        script += f" && ({outil_ipt} -C DOCKER-USER -j {CHAINE} 2>/dev/null || {outil_ipt} -I DOCKER-USER -j {CHAINE})"
        code, _, err = hote_sh(script)
        if code != 0:
            ok = False
            print("pare-feu :", outil_ipt, err[:200], flush=True)
    return ok


def url_invites(d):
    ip = d.get("adresse") or adresse_wifi()[1]
    return f"http://{ip}/?c={d['code']}" if ip else None


@app.get("/api/invites")
def api_invites(request: Request):
    exiger_admin(request)
    try:
        synchroniser_outils_invites()
    except HTTPException:
        pass
    d = invites.lire()
    return {"actif": d["actif"], "code": d["code"], "url": url_invites(d), "nom_local": f"http://{nom_hote()}.local",
            "dossiers": d["dossiers"], "wifi": d["wifi"],
            "outils": [{**o, "wifi": bool(d["wifi"].get(o["id"]))} for o in d["outils"]]}


@app.get("/api/invites/qr.svg")
def api_invites_qr(request: Request):
    exiger_admin(request)
    import io
    import segno
    url = url_invites(invites.lire())
    if not url:
        raise HTTPException(404, "Adresse du serveur introuvable.")
    tampon = io.BytesIO()
    segno.make(url, error="m").save(tampon, kind="svg", scale=8, border=2, dark="#111418", light="#ffffff")
    return Response(tampon.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@app.post("/api/invites")
async def api_invites_regler(request: Request):
    j = exiger_admin(request)
    corps = await request.json()
    action = corps.get("action")
    if action == "actif":
        actif = bool(corps.get("actif"))
        invites.modifier(lambda d: d.update(actif=actif))
        if actif:
            invites.preparer_dossier_invites()
        texte = f"Accès invités {'ouvert' if actif else 'fermé'}"
    elif action == "code":
        invites.nouveau_code()
        texte = "Nouveau code invités (les invités déjà entrés devront le ressaisir)"
    elif action == "wifi":
        pid, ouvert = corps.get("outil"), bool(corps.get("wifi"))
        if pid not in PORTS:
            raise HTTPException(400, "Outil inconnu.")
        invites.modifier(lambda d: d["wifi"].update({pid: ouvert}))
        if not appliquer_pare_feu():
            raise HTTPException(500, "Le réglage est noté, mais le filtre réseau n'a pas pu être appliqué.")
        texte = f"{NOMS.get(pid, (pid,))[0]} {'joignable' if ouvert else 'plus joignable'} depuis le Wi-Fi"
    elif action == "ajouter_dossier":
        virtuel, mode = chemin_propre(corps.get("chemin", "")), corps.get("mode", "lecture")
        reel, _ = fs(lambda: disque.resoudre(virtuel, racines_partageables(), set()))
        if not reel or not os.path.isdir(reel):
            raise HTTPException(404, "Dossier introuvable.")
        if mode not in ("lecture", "depot"):
            raise HTTPException(400, "Mode inconnu.")
        nom = virtuel.rstrip("/").rsplit("/", 1)[-1]

        def f(d):
            if any(x["chemin"] == reel for x in d["dossiers"]):
                raise HTTPException(409, "Ce dossier est déjà partagé.")
            d["dossiers"].append({"id": uuid.uuid4().hex[:8], "nom": nom, "chemin": reel, "mode": mode})
        invites.modifier(f)
        texte = f"Dossier « {nom} » partagé avec les invités ({'dépôt autorisé' if mode == 'depot' else 'lecture seule'})"
    elif action in ("retirer_dossier", "mode_dossier"):
        did = corps.get("id")
        mode = corps.get("mode")

        def f(d):
            x = next((x for x in d["dossiers"] if x["id"] == did), None)
            if not x:
                raise HTTPException(404, "Dossier introuvable.")
            if action == "retirer_dossier":
                d["dossiers"] = [y for y in d["dossiers"] if y["id"] != did]
            elif mode in ("lecture", "depot"):
                x["mode"] = mode
            return x["nom"]
        nom = invites.modifier(f)
        texte = f"Dossier « {nom} » " + ("retiré des invités" if action == "retirer_dossier" else f"en {'dépôt autorisé' if mode == 'depot' else 'lecture seule'} pour les invités")
    else:
        raise HTTPException(400, "Action inconnue.")
    journal.noter("invite", f"{texte}, par {j['identifiant']}", request.state.nom)
    return {"ok": True}


@app.get("/api/invites/parcourir")
def api_invites_parcourir(request: Request, chemin: str = "/"):
    """Les dossiers du serveur, pour choisir ceux à partager (lecture directe du disque)."""
    exiger_admin(request)
    chemin = chemin_propre(chemin)
    if chemin == "/":
        invites.preparer_dossier_invites()
        toutes = {**disque.racines(), **racines_partageables()}
        elements = [{"nom": n, "chemin": "/" + n} for n, c in toutes.items() if os.path.isdir(c)]
        return {"chemin": "/", "elements": elements}
    d = fs(lambda: disque.lister(chemin, racines_partageables(), set()))
    return {"chemin": chemin, "elements": [{"nom": e["nom"], "chemin": e["chemin"]} for e in d["elements"] if e["dossier"]]}


def racines_partageables():
    """Disques branchés et dossier Invités, en plus des dossiers du serveur."""
    r = {n: c for n, c in disques_connus().items() if n in disques_branches()}
    r["Invités"] = invites.DOSSIER_INVITES
    return r


# ---------------------------------------------------------------- surveillance (tourne en fond)
COURANT = {"secteur": None, "batterie": None, "etat": None, "arret_prevu": False}
SURV = {"disques_pleins": set(), "pannes": {}, "redemarrage_signale": False, "maj_signalee": None,
        "derniere_purge": 0, "dernier_lien_nc": 0}


def nom_serveur():
    return "Serveur-démo" if DEMO else (hote(["hostname"])[1].strip().capitalize() or "Le serveur")


def lire_courant():
    script = ('for f in /sys/class/power_supply/*; do [ -e "$f/type" ] || continue; '
              'echo "$(cat $f/type)|$(cat $f/online 2>/dev/null)|$(cat $f/capacity 2>/dev/null)|$(cat $f/status 2>/dev/null)'
              '|$(cat $f/power_now 2>/dev/null)|$(cat $f/current_now 2>/dev/null)|$(cat $f/voltage_now 2>/dev/null)"; done')
    _, sortie, _ = hote_sh(script, timeout=10)
    secteur, batterie, etat = None, None, None
    for ligne in sortie.splitlines():
        t, en_ligne, capa, statut, puissance, intensite, tension = (ligne.split("|") + [""] * 7)[:7]
        if t == "Mains" and en_ligne.strip().isdigit():
            secteur = (secteur or False) or en_ligne.strip() == "1"
        elif t == "Battery" and capa.strip().isdigit():
            batterie, etat = int(capa), statut.strip()
            # Consommation : mesurable seulement sur batterie (sur secteur, la batterie ne voit pas ce que tire le PC).
            try:
                w = int(puissance) / 1e6 if puissance.strip() else int(intensite) * int(tension) / 1e12
            except ValueError:
                w = None
            if etat == "Discharging" and w and 0.5 < w < 200:
                mesure_conso(round(w, 1))
    if secteur is None and etat:
        secteur = etat != "Discharging"
    return secteur, batterie, etat


def mesure_conso(w):
    """Garde la dernière consommation mesurée (et la plus basse, celle du repos), dans donnees/."""
    try:
        with open(FICHIER_CONSO) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d["derniere"] = {"w": w, "t": int(time.time())}
    if not d.get("repos") or w < d["repos"]["w"]:
        d["repos"] = {"w": w, "t": int(time.time())}
    with open(FICHIER_CONSO, "w") as f:
        json.dump(d, f)


def lire_conso():
    try:
        with open(FICHIER_CONSO) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def surveiller_courant():
    secteur, batterie, etat = lire_courant()
    avant = COURANT["secteur"]
    COURANT.update(batterie=batterie, etat=etat)
    if secteur is None:
        return
    COURANT["secteur"] = secteur
    n = nom_serveur()
    if avant is True and not secteur:
        notifs.envoyer("courant", "Coupure de courant", f"{n} tourne sur batterie ({batterie} %). Il s'éteindra proprement sous {SEUIL_BATTERIE} %.", "/#/systeme", famille="courant")
    elif avant is False and secteur:
        if COURANT["arret_prevu"]:
            hote(["systemctl", "stop", "jabba-arret.timer"])
            COURANT["arret_prevu"] = False
        notifs.envoyer("courant", "Courant revenu", f"{n} est de nouveau sur secteur ({batterie} %).", "/#/systeme", famille="courant")
    if not secteur and batterie is not None and batterie <= SEUIL_BATTERIE and not COURANT["arret_prevu"]:
        COURANT["arret_prevu"] = True
        notifs.envoyer("courant", "Batterie faible : arrêt dans 1 minute",
                       f"{n} s'éteint proprement pour protéger les disques. Rallume-le avec son bouton quand le courant est revenu.", "/#/systeme", famille="courant")
        hote(["systemd-run", "--unit=jabba-arret", "--collect", "--on-active=60", "systemctl", "poweroff"])


def surveiller_espace():
    chemins = {"/": "Disque système", "/srv/data": "Disque de données"}
    for nom, chemin in disques_connus().items():
        if hote(["mountpoint", "-q", chemin], timeout=10)[0] == 0:
            chemins[chemin] = nom
    for chemin, u in espace(list(chemins)).items():
        p = 100 * u["utilise"] / max(1, u["total"])
        if p >= 90 and chemin not in SURV["disques_pleins"]:
            SURV["disques_pleins"].add(chemin)
            notifs.envoyer("disque_plein", f"{chemins.get(chemin, chemin)} presque plein", f"Rempli à {round(p)} %. Fais de la place, ou vide la corbeille.", "/#/disques", famille="disque")
        elif p < 85:
            SURV["disques_pleins"].discard(chemin)


def surveiller_outils():
    en_cours = {t.get("outil") for t in TACHES.values() if t["etat"] == "en cours"}
    for o in lister_outils():
        pid = o["id"]
        souci = o["etat"] in ("en panne", "partiel") and pid not in en_cours
        vu = SURV["pannes"].get(pid, 0)
        if souci:
            SURV["pannes"][pid] = vu + 1
            if vu + 1 == 2:  # deux passages de suite (2 minutes) : ce n'est pas un simple redémarrage
                notifs.envoyer("outil_panne", f"{o['nom']} en panne", "Ouvre l'Établi et essaie « Redémarrer ».", "/#/outils", famille="outil")
        else:
            if vu >= 2 and o["etat"] == "actif":
                notifs.envoyer("outil_panne", f"{o['nom']} est reparti", "", "/#/outils", famille="outil")
            SURV["pannes"].pop(pid, None)


def surveiller_maj():
    if hote(["test", "-f", "/var/run/reboot-required"])[0] == 0:
        if not SURV["redemarrage_signale"]:
            SURV["redemarrage_signale"] = True
            notifs.envoyer("mises_a_jour", "Redémarrage en attente", "Une mise à jour du système le demande. Il se fera seul à 4 h.", "/#/systeme", famille="systeme")
    else:
        SURV["redemarrage_signale"] = False
    if DEPOT:
        der = maj.derniere(DEPOT)
        if der and maj.plus_recente(VERSION, der["version"]) and SURV["maj_signalee"] != der["version"]:
            SURV["maj_signalee"] = der["version"]
            notifs.envoyer("mises_a_jour", f"Appli Jabba {der['version']} disponible", "Installe-la depuis l'écran Système.", "/#/systeme", famille="appli")


def scruter_disques():
    reveiller_disques()   # un disque rebranché est ouvert tout seul, sans attendre qu'on le consulte
    disques_auto.scruter(hote, hote_entree, lambda: source() == "nextcloud",
                         lambda t, ti, tx, u: notifs.envoyer(t, ti, tx, u, famille="disque"),
                         lambda: list(disques_env()) + list(disque.racines()))


def relier_disques_nextcloud():
    if source() != "nextcloud":
        return
    for x in disques_auto.lire()["disques"]:
        if not x.get("nextcloud"):
            disques_auto.relier_nextcloud(hote, x)


def surveiller():
    if DEMO:
        return
    tour = 0
    while True:
        etapes = [scruter_disques]
        if tour % 4 == 0:   # toutes les minutes
            etapes += [lecture.nettoyer, lambda: MAJ["unite"] and not MAJ["signale"] and etat_maj(), surveiller_courant, surveiller_espace, surveiller_outils, synchroniser_outils_invites, appliquer_pare_feu]
        if tour % 40 == 0:  # toutes les 10 minutes
            etapes += [relier_disques_nextcloud]
        if tour % 240 == 0:  # toutes les heures
            etapes += [surveiller_maj]
        if time.time() - SURV["derniere_purge"] > 86400:
            SURV["derniere_purge"] = time.time()
            etapes += [disque.purger]
        for etape in etapes:
            try:
                etape()
            except Exception as e:  # noqa: BLE001 — la surveillance ne s'arrête jamais
                print("surveillance:", etape.__name__, e, flush=True)
        tour += 1
        time.sleep(15)


@app.on_event("startup")
def demarrage():
    fichier = os.path.join(os.path.dirname(journal.FICHIER), "version.txt")
    try:
        avant = open(fichier).read().strip()
    except OSError:
        avant = None
    if avant != VERSION:
        journal.noter("appli", f"Appli Jabba {VERSION} installée" + (f" (avant : {avant})" if avant else ""))
        try:
            os.makedirs(os.path.dirname(fichier), exist_ok=True)
            open(fichier, "w").write(VERSION)
        except OSError:
            pass
    threading.Thread(target=surveiller, daemon=True, name="surveillance").start()


# ---------------------------------------------------------------- interface
@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(STATIC, "sw.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
@app.get("/{reste:path}")
def page(reste: str = ""):
    if reste.startswith("api/"):
        raise HTTPException(404)
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})
