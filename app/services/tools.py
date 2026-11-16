"""services.tools"""
import json
import re
from urllib.parse import urljoin
import httpx
from fastapi import HTTPException
import config
from host import commands


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
# Jamais ouverts aux invités du Wi-Fi : ils donnent la main sur le serveur.
JAMAIS_WIFI = {"dockge"}
ICONES_SECOURS = ["/web/favicon.png", "/web/favicon.ico", "/favicon.ico", "/favicon.png",
                  "/static/img/icons/favicon-32x32.png", "/core/img/favicon.ico", "/icon.svg"]
HORS_APPLI = {"nextcloud"}


def lister_outils():
    if config.DEMO:
        from services.demo_data import OUTILS
        return OUTILS
    code, sortie, err = commands.hote(["docker", "ps", "-a", "--format", "{{json .}}"])
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
            "lien": config.LIENS.get(pid), "dossier": p["dossier"], "conteneurs": p["conteneurs"],
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
