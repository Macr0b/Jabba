"""Appli Jabba — le moteur.

Tourne sur Jabba (réseau de l'hôte, port 127.0.0.1:8090) et n'est joignable
que par `tailscale serve`, qui ajoute l'identité Tailscale de la personne.
Les commandes système sont lancées sur Jabba lui-même via nsenter.
"""
import asyncio
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
import disque
import disques_auto
import journal
import maj
import notifs
from fastapi import FastAPI, HTTPException, Request
from starlette.datastructures import UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from xml.sax.saxutils import escape as xml_escape
from fastapi.staticfiles import StaticFiles

VERSION = "0.5.1"
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


# ---------------------------------------------------------------- sécurité
@app.middleware("http")
async def garde(request: Request, call_next):
    if DEMO:
        request.state.user = "demo@exemple.fr"
        request.state.nom = "Alex"
    else:
        login = (request.headers.get("tailscale-user-login") or "").lower()
        if not login or login not in UTILISATEURS:
            return JSONResponse({"erreur": "Accès réservé à tes appareils Tailscale."}, status_code=403)
        request.state.user = login
        request.state.nom = request.headers.get("tailscale-user-name") or login
    # Anti-CSRF : toute action doit venir de l'appli (en-tête impossible à
    # ajouter depuis un autre site sans autorisation CORS, jamais accordée).
    if request.method not in ("GET", "HEAD") and request.headers.get("x-jabba") != "1":
        return JSONResponse({"erreur": "Requête refusée."}, status_code=403)
    reponse = await call_next(request)
    reponse.headers.setdefault("X-Frame-Options", "DENY")
    reponse.headers["X-Content-Type-Options"] = "nosniff"
    reponse.headers["Referrer-Policy"] = "no-referrer"
    return reponse


# ---------------------------------------------------------------- hôte
def hote(cmd, timeout=30, entree=None):
    """Lance une commande sur le serveur (pas dans le conteneur)."""
    complet = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--"] + cmd
    r = subprocess.run(complet, capture_output=True, text=True, timeout=timeout, input=entree)
    return r.returncode, r.stdout, r.stderr


def hote_entree(cmd, entree, timeout=30):
    return hote(cmd, timeout=timeout, entree=entree)


# ---------------------------------------------------------------- mot de passe admin
def exiger_admin(request: Request):
    if admin.jeton_valide(request.headers.get("x-admin")):
        return
    raise HTTPException(401, {"admin": True, "message": "Mot de passe du serveur demandé."})


def admin_user():
    """Le compte du serveur dont le mot de passe protège les actions admin (par défaut : le premier compte créé)."""
    if ADMIN_USER or DEMO:
        return ADMIN_USER or "demo"
    _, sortie, _ = hote(["getent", "passwd", "1000"])
    return sortie.split(":", 1)[0] or "root"


def verifier_admin(mot_de_passe):
    if DEMO:
        return mot_de_passe == "demo-mot-de-passe"
    return admin.verifier_mot_de_passe(hote, admin_user(), mot_de_passe)


@app.post("/api/admin/connexion")
async def api_admin_connexion(request: Request):
    corps = await request.json()
    if admin.bloque():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    if not verifier_admin(corps.get("mot_de_passe", "")):
        echec_admin(request)
        raise HTTPException(403, "Mot de passe incorrect.")
    journal.noter("admin", "Mot de passe du serveur saisi", request.state.nom)
    return {"jeton": admin.nouveau_jeton(), "duree": admin.DUREE_JETON}


def echec_admin(request):
    journal.noter("admin", "Mot de passe du serveur incorrect", request.state.nom)
    if admin.bloque():
        notifs.envoyer("securite", "Mot de passe raté 5 fois",
                       f"Depuis l'appareil de {request.state.nom}. Les actions admin sont bloquées 10 minutes.", "/#/systeme")


@app.get("/api/admin/etat")
def api_admin_etat():
    return {"code_existe": admin.code_existe(), "utilisateur": admin_user()}


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
    corps = await request.json()
    if admin.bloque():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    if not verifier_admin(corps.get("ancien", "")):
        echec_admin(request)
        raise HTTPException(403, "L'ancien mot de passe est incorrect.")
    _changer(corps.get("nouveau", ""))
    notifs.envoyer("securite", "Mot de passe du serveur changé", f"Par {request.state.nom}.", "/#/systeme", famille="admin")
    return {"ok": True}


@app.post("/api/admin/code")
def api_admin_code(request: Request):
    exiger_admin(request)
    journal.noter("admin", "Nouveau code de secours créé", request.state.nom)
    return {"code": admin.generer_code()}


@app.post("/api/admin/secours")
async def api_admin_secours(request: Request):
    corps = await request.json()
    if admin.bloque():
        raise HTTPException(429, "Trop d'essais. Réessaie dans 10 minutes.")
    if not admin.verifier_code(corps.get("code", "")):
        echec_admin(request)
        raise HTTPException(403, "Code de secours incorrect.")
    _changer(corps.get("nouveau", ""))
    notifs.envoyer("securite", "Mot de passe du serveur changé", f"Avec le code de secours, par {request.state.nom}.", "/#/systeme", famille="admin")
    return {"ok": True, "code": admin.generer_code()}


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
    "filebrowser": ("File Browser", "Tous les dossiers du disque de données"),
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
    "filebrowser": "adresse-fichiers",
}
# Où chaque outil range son icône : l'appli affiche le logo servi par l'outil lui-même.
# Port local de chaque outil : l'appli y va chercher le logo que l'outil fournit lui-même.
PORTS = {"jellyfin": 8096, "nextcloud": 8081, "filebrowser": 8082, "dockge": 5001}
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
    return {"login": request.state.user, "nom": request.state.nom, "version": VERSION, "serveur": serveur}


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
        "version_appli": VERSION, "courant": COURANT,
    }


@app.post("/api/systeme/redemarrer")
def api_reboot(request: Request):
    exiger_admin(request)
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
            hote(["ls", chemin], timeout=10)


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
    disques = {n: connus[n] for n in branches}
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
    for morceau in os.environ.get("DISQUES", "R2-D2=/srv/externe").split(","):
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
        hote(["ls", chemin], timeout=10)  # réveille l'ouverture automatique
        code, _, _ = hote(["mountpoint", "-q", chemin], timeout=10)
        if code == 0:
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
    exiger_admin(request)
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
    exiger_admin(request)
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
        notifs.abonner(corps.get("abonnement"), contact, str(corps.get("appareil", "")))
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
                "derniere": {"version": "0.6.0", "tag": "v0.6.0", "notes": "- Correctifs\n- Aperçus plus rapides", "date": "2026-11-20T10:00:00Z"},
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
    exiger_admin(request)
    if DEMO:
        return {"ok": True}
    der = maj.derniere(DEPOT, forcer=True)
    d = dossier_appli()
    if not (der and d and maj.plus_recente(VERSION, der["version"])):
        raise HTTPException(409, "Pas de nouvelle version à installer.")
    script = maj.script(d, der["tag"])
    journal.noter("appli", f"Mise à jour de l'appli vers {der['version']} lancée", request.state.nom)
    # Lancée par le serveur lui-même : elle continue pendant que l'appli redémarre.
    code, _, err = hote(["systemd-run", f"--unit=jabba-maj-{int(time.time())}", "--collect", "sh", "-c", script])
    if code != 0:
        raise HTTPException(500, "Lancement impossible : " + err[:200])
    return {"ok": True, "version": der["version"]}


# ---------------------------------------------------------------- surveillance (tourne en fond)
COURANT = {"secteur": None, "batterie": None, "etat": None, "arret_prevu": False}
SURV = {"disques_pleins": set(), "pannes": {}, "redemarrage_signale": False, "maj_signalee": None,
        "derniere_purge": 0, "dernier_lien_nc": 0}


def nom_serveur():
    return "Serveur-démo" if DEMO else (hote(["hostname"])[1].strip().capitalize() or "Le serveur")


def lire_courant():
    script = ('for f in /sys/class/power_supply/*; do [ -e "$f/type" ] || continue; '
              'echo "$(cat $f/type)|$(cat $f/online 2>/dev/null)|$(cat $f/capacity 2>/dev/null)|$(cat $f/status 2>/dev/null)"; done')
    _, sortie, _ = hote_sh(script, timeout=10)
    secteur, batterie, etat = None, None, None
    for ligne in sortie.splitlines():
        t, en_ligne, capa, statut = (ligne.split("|") + ["", "", "", ""])[:4]
        if t == "Mains" and en_ligne.strip().isdigit():
            secteur = (secteur or False) or en_ligne.strip() == "1"
        elif t == "Battery" and capa.strip().isdigit():
            batterie, etat = int(capa), statut.strip()
    if secteur is None and etat:
        secteur = etat != "Discharging"
    return secteur, batterie, etat


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
            etapes += [surveiller_courant, surveiller_espace, surveiller_outils]
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
