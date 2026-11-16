"""routes.files"""
import asyncio
import mimetypes
import os
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote
from services import disk_browser
from services import activity_log
from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from xml.sax.saxutils import escape as xml_escape
import config
from security import guard
from services import files_source
from services import home_data
from services import nextcloud_files
from fastapi import APIRouter

router = APIRouter()

@router.get("/api/fichiers")
async def api_fichiers(chemin: str = "/"):
    chemin = nextcloud_files.chemin_propre(chemin)
    if files_source.source() == "disque":
        d = files_source.fs(lambda: disk_browser.lister(chemin, *files_source.disques_fs()))
    else:
        nextcloud_files.nc_pret()
        d = await nextcloud_files.lire_dossier(chemin)
        d["source"] = "nextcloud"
    if chemin != "/":
        home_data.noter_visite(chemin)
    d["favori"] = chemin in home_data.lire_donnees()["favoris"]
    return d


@router.get("/api/accueil/dossiers")
async def api_accueil_dossiers():
    if files_source.source() == "disque":
        racine = files_source.fs(lambda: disk_browser.lister("/", *files_source.disques_fs()))
    else:
        nextcloud_files.nc_pret()
        racine = await nextcloud_files.lire_dossier("/")
    branches, connus = files_source.disques_branches(), files_source.disques_connus()
    essentiels = [{"nom": "Mes fichiers", "chemin": "/", "type": "racine"}]
    for e in racine["elements"]:
        if files_source.source() == "disque" and e["dossier"]:
            essentiels.append({"nom": e["nom"], "chemin": e["chemin"], "type": "disque" if e["externe"] else "espace"})
        elif e["dossier"] and e["externe"]:
            if e["nom"] in connus:
                if e["nom"] in branches:
                    essentiels.append({"nom": e["nom"], "chemin": e["chemin"], "type": "disque"})
            else:
                essentiels.append({"nom": e["nom"], "chemin": e["chemin"], "type": "espace"})
    d = home_data.lire_donnees()
    favoris = [{"nom": c.rsplit("/", 1)[-1], "chemin": c} for c in d["favoris"]]
    limite = time.time() - home_data.FENETRE_SUGGESTION
    suggestions = []
    for c, ts in d["visites"].items():
        n = sum(1 for t in ts if t > limite)
        if n >= home_data.SEUIL_SUGGESTION and c not in d["favoris"] and c not in d["ignores"]:
            suggestions.append({"nom": c.rsplit("/", 1)[-1], "chemin": c, "ouvertures": n})
    suggestions.sort(key=lambda s: -s["ouvertures"])
    return {"essentiels": essentiels, "favoris": favoris, "suggestions": suggestions[:2]}


@router.post("/api/favoris")
async def api_favoris(request: Request):
    corps = await request.json()
    chemin = nextcloud_files.chemin_propre(corps.get("chemin", ""))
    action = corps.get("action")
    if chemin == "/" or action not in ("ajouter", "retirer", "ignorer"):
        raise HTTPException(400, "Demande invalide")
    with home_data.VERROU:
        d = home_data.lire_donnees()
        if action == "ajouter" and chemin not in d["favoris"]:
            d["favoris"].append(chemin)
        elif action == "retirer":
            d["favoris"] = [c for c in d["favoris"] if c != chemin]
        elif action == "ignorer" and chemin not in d["ignores"]:
            d["ignores"].append(chemin)
        home_data.ecrire_donnees(d)
    return {"ok": True, "favori": chemin in d["favoris"]}


@router.post("/api/fichiers/dossier")
async def api_mkdir(request: Request):
    corps = await request.json()
    chemin = nextcloud_files.chemin_propre(corps.get("chemin", ""))
    if files_source.source() == "disque":
        files_source.fs(lambda: disk_browser.creer_dossier(chemin, *files_source.disques_fs()))
        return {"ok": True}
    nextcloud_files.nc_pret()
    if config.DEMO:
        return {"ok": True}
    async with nextcloud_files.client() as c:
        r = await c.request("MKCOL", nextcloud_files.dav_url(chemin))
    if r.status_code == 405:
        raise HTTPException(409, "Un dossier porte déjà ce nom.")
    await nextcloud_files.verifier(r, "Création du dossier")
    return {"ok": True}


@router.post("/api/fichiers/envoyer")
async def api_envoyer(request: Request, chemin: str = "/"):
    chemin = nextcloud_files.chemin_propre(chemin)
    formulaire = await request.form()
    envoyes = []
    if files_source.source() == "disque":
        params = files_source.disques_fs()
        for f in formulaire.getlist("fichiers"):
            if not isinstance(f, UploadFile) or not f.filename:
                continue
            nom = os.path.basename(f.filename.replace("\\", "/"))
            reel = files_source.fs(lambda: disk_browser.chemin_envoi(chemin, nom, *params))
            if os.path.exists(reel):
                raise HTTPException(409, f"« {nom} » existe déjà dans ce dossier.")
            with open(reel, "wb") as sortie:
                while bloc := await f.read(1024 * 1024):
                    sortie.write(bloc)
            disk_browser.fin_envoi(reel)
            envoyes.append(nom)
        noter_envoi(request, chemin, envoyes)
        return {"envoyes": envoyes}
    nextcloud_files.nc_pret()
    for f in formulaire.getlist("fichiers"):
        if not isinstance(f, UploadFile) or not f.filename:
            continue
        nom = os.path.basename(f.filename.replace("\\", "/"))
        if config.DEMO:
            envoyes.append(nom)
            continue

        async def morceaux(fichier=f):
            while bloc := await fichier.read(1024 * 1024):
                yield bloc

        async with nextcloud_files.client() as c:
            r = await c.put(nextcloud_files.dav_url(f"{chemin}/{nom}"), content=morceaux())
        await nextcloud_files.verifier(r, f"Envoi de {nom}")
        envoyes.append(nom)
    noter_envoi(request, chemin, envoyes)
    return {"envoyes": envoyes}


def noter_envoi(request, chemin, envoyes):
    if envoyes:
        quoi = f"« {envoyes[0]} »" if len(envoyes) == 1 else f"{len(envoyes)} fichiers"
        activity_log.noter("fichier", f"{quoi} envoyé{'s' if len(envoyes) > 1 else ''} dans {chemin}", request.state.nom)


@router.get("/api/fichiers/telecharger")
async def api_telecharger(chemin: str):
    chemin = nextcloud_files.chemin_propre(chemin)
    if files_source.source() == "disque":
        reel = files_source.fs(lambda: disk_browser.chemin_lecture(chemin, *files_source.disques_fs()))
        return FileResponse(reel, filename=os.path.basename(reel))
    nextcloud_files.nc_pret()
    if config.DEMO:
        raise HTTPException(404, "Pas de fichiers en démo")
    c = nextcloud_files.client()
    req = c.build_request("GET", nextcloud_files.dav_url(chemin))
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


@router.post("/api/fichiers/deplacer")
async def api_deplacer(request: Request):
    corps = await request.json()
    de, vers = nextcloud_files.chemin_propre(corps.get("de", "")), nextcloud_files.chemin_propre(corps.get("vers", ""))
    if de == "/" or vers == "/":
        raise HTTPException(400, "Chemin invalide")
    if files_source.source() == "disque":
        files_source.fs(lambda: disk_browser.deplacer(de, vers, *files_source.disques_fs()))
        return {"ok": True}
    nextcloud_files.nc_pret()
    if config.DEMO:
        return {"ok": True}
    async with nextcloud_files.client() as c:
        r = await c.request("MOVE", nextcloud_files.dav_url(de), headers={"Destination": nextcloud_files.dav_url(vers), "Overwrite": "F"})
    await nextcloud_files.verifier(r, "Renommage")
    return {"ok": True}


@router.post("/api/fichiers/supprimer")
async def api_supprimer(request: Request):
    corps = await request.json()
    chemin = nextcloud_files.chemin_propre(corps.get("chemin", ""))
    if chemin == "/" or len([m for m in chemin.split("/") if m]) < 2 and files_source.source() == "disque":
        raise HTTPException(400, "Ce dossier ne peut pas être supprimé.")
    if files_source.source() == "disque":
        params = files_source.disques_fs()
        if files_source.fs(lambda: disk_browser.est_dossier(chemin, *params)):
            guard.exiger_admin(request)  # supprimer un dossier entier demande le mot de passe
        files_source.fs(lambda: disk_browser.supprimer(chemin, *params))
        activity_log.noter("fichier", f"{chemin} mis à la corbeille", request.state.nom)
        return {"ok": True}
    nextcloud_files.nc_pret()
    if config.DEMO:
        if corps.get("dossier"):
            guard.exiger_admin(request)
        return {"ok": True}
    if await nextcloud_files.est_dossier_nc(chemin):
        guard.exiger_admin(request)
    async with nextcloud_files.client() as c:
        r = await c.delete(nextcloud_files.dav_url(chemin))
    await nextcloud_files.verifier(r, "Suppression")
    activity_log.noter("fichier", f"{chemin} mis à la corbeille", request.state.nom)
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


@router.get("/api/fichiers/apercu")
async def api_apercu(chemin: str, taille: int = 128):
    chemin = nextcloud_files.chemin_propre(chemin)
    taille = 256 if taille > 128 else 128
    if config.DEMO and files_source.source() != "disque":
        raise HTTPException(404)
    if files_source.source() == "disque":
        reel = files_source.fs(lambda: disk_browser.chemin_lecture(chemin, *files_source.disques_fs()))
        try:
            cache = await asyncio.to_thread(disk_browser.vignette, reel, taille)
        except disk_browser.Refus as e:
            raise HTTPException(e.code, e.message)
        except Exception:  # noqa: BLE001 — image abîmée ou format inconnu
            raise HTTPException(404, "Pas d'aperçu")
        return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
    nextcloud_files.nc_pret()
    async with nextcloud_files.client() as c:
        r = await c.get(config.NC_URL + "/index.php/core/preview.png",
                        params={"file": chemin, "x": taille, "y": taille, "a": "1", "mode": "cover"})
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
        raise HTTPException(404, "Pas d'aperçu")
    return Response(r.content, media_type=r.headers["content-type"], headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/fichiers/voir")
async def api_voir(chemin: str, request: Request):
    """Le fichier lui-même, à afficher dans l'appli (photos, PDF, vidéos, musique, textes)."""
    chemin = nextcloud_files.chemin_propre(chemin)
    nom = chemin.rsplit("/", 1)[-1]
    type_ = type_visible(nom)
    if not type_:
        raise HTTPException(415, "Ce type de fichier ne s'affiche pas dans l'appli : télécharge-le.")
    if config.DEMO and files_source.source() != "disque":
        raise HTTPException(404, "Pas de fichiers en démo")
    if files_source.source() == "disque":
        reel = files_source.fs(lambda: disk_browser.chemin_lecture(chemin, *files_source.disques_fs()))
        return FileResponse(reel, media_type=type_, headers=entetes_visionneuse(type_, nom))
    nextcloud_files.nc_pret()
    c = nextcloud_files.client()
    entetes = {"Range": request.headers["range"]} if "range" in request.headers else {}
    r = await c.send(c.build_request("GET", nextcloud_files.dav_url(chemin), headers=entetes), stream=True)
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


@router.get("/api/recherche")
async def api_recherche(q: str = ""):
    """Cherche par nom, partout : dans Nextcloud et directement sur les disques du serveur."""
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(400, "Tape au moins 2 lettres.")
    if config.DEMO and files_source.source() != "disque":
        from services.demo_data import recherche
        return recherche(q)
    disques, lecture_seule = files_source.disques_fs()
    if files_source.source() == "disque":
        res, complet = await asyncio.to_thread(disk_browser.rechercher, q, disques, lecture_seule)
        return {"elements": res, "complet": complet}
    nextcloud_files.nc_pret()
    async with nextcloud_files.client() as c:
        r = await c.request("SEARCH", config.NC_URL + "/remote.php/dav/", headers={"Content-Type": "text/xml"},
                            content=nextcloud_files.SEARCH.format(user=xml_escape(config.NC_USER), texte=xml_escape(q)).encode())
    res = []
    if r.status_code < 400:
        for rep in ET.fromstring(r.content).findall("d:response", nextcloud_files.DAV_NS):
            if rep.find("d:propstat/d:prop", nextcloud_files.DAV_NS) is not None:
                res.append(nextcloud_files.item_dav(rep))
    # Les dossiers du serveur vus dans Nextcloud (films, disques…) ne sont pas
    # tous indexés par Nextcloud : on les parcourt directement.
    racine = await nextcloud_files.lire_dossier("/")
    externes = {e["nom"] for e in racine["elements"] if e["externe"]}
    direct, complet = await asyncio.to_thread(disk_browser.rechercher, q, disques, lecture_seule, externes)
    vus = {e["chemin"] for e in res}
    res += [e for e in direct if e["chemin"] not in vus]
    res.sort(key=lambda e: (not e["nom"].lower().startswith(q.lower()), not e["dossier"], e["nom"].lower()))
    return {"elements": res[:100], "complet": complet and len(res) <= 100}


@router.get("/api/corbeille")
async def api_corbeille():
    if config.DEMO and files_source.source() != "disque":
        from services.demo_data import CORBEILLE
        return {"elements": CORBEILLE, "garde": 30}
    elements = await asyncio.to_thread(disk_browser.lister_corbeille)
    if files_source.source() == "nextcloud":
        nextcloud_files.nc_pret()
        elements = await nextcloud_files.corbeille_nc() + elements
    elements.sort(key=lambda e: -e["supprime"])
    return {"elements": elements, "garde": 30}


@router.post("/api/corbeille/restaurer")
async def api_corbeille_restaurer(request: Request):
    corps = await request.json()
    ident, src = str(corps.get("id", "")), corps.get("source")
    if config.DEMO and src != "disque":
        return {"ok": True, "chemin": "/Documents"}
    if src == "disque":
        chemin = files_source.fs(lambda: disk_browser.restaurer(ident, *files_source.disques_fs()))
    else:
        nextcloud_files.nc_pret()
        if "/" in ident:
            raise HTTPException(400, "Élément invalide")
        async with nextcloud_files.client() as c:
            r = await c.request("MOVE", nextcloud_files.url_corbeille(ident),
                                headers={"Destination": config.NC_URL + f"/remote.php/dav/trashbin/{quote(config.NC_USER)}/restore/{quote(ident, safe='')}"})
        await nextcloud_files.verifier(r, "Restauration")
        chemin = corps.get("origine") or "/"
    activity_log.noter("fichier", f"{chemin} restauré depuis la corbeille", request.state.nom)
    return {"ok": True, "chemin": chemin}


@router.post("/api/corbeille/effacer")
async def api_corbeille_effacer(request: Request):
    """Effacer pour de bon : un élément, ou toute la corbeille. Demande le mot de passe du serveur."""
    guard.exiger_admin(request, fort=True)
    corps = await request.json()
    ident, src, tout = str(corps.get("id", "")), corps.get("source"), bool(corps.get("tout"))
    if config.DEMO and files_source.source() != "disque":
        return {"ok": True}
    if tout:
        await asyncio.to_thread(disk_browser.vider)
        if files_source.source() == "nextcloud" and config.NC_USER:
            async with nextcloud_files.client() as c:
                await c.delete(nextcloud_files.url_corbeille())
        activity_log.noter("fichier", "Corbeille vidée", request.state.nom)
        return {"ok": True}
    if src == "disque":
        files_source.fs(lambda: disk_browser.effacer(ident))
    else:
        nextcloud_files.nc_pret()
        if "/" in ident:
            raise HTTPException(400, "Élément invalide")
        async with nextcloud_files.client() as c:
            r = await c.delete(nextcloud_files.url_corbeille(ident))
        await nextcloud_files.verifier(r, "Effacement")
    activity_log.noter("fichier", f"« {corps.get('nom', ident)} » effacé définitivement", request.state.nom)
    return {"ok": True}
