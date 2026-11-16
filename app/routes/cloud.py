"""routes.cloud"""
from security import accounts
from services import activity_log
from services import nextcloud_accounts
from fastapi import HTTPException, Request
import config
from host import commands
from security import guard
from services import measurements
from fastapi import APIRouter

router = APIRouter()

def fgo(go):
    """12.5 → « 12,5 Go » ; None → « illimité »."""
    return "illimité" if go is None else str(round(float(go), 1)).removesuffix(".0").replace(".", ",") + " Go"


def _nuage(f, *a):
    try:
        return f(commands.occ, *a)
    except nextcloud_accounts.Erreur as e:
        raise HTTPException(400, str(e))


@router.get("/api/nuage")
def api_nuage(request: Request):
    j = guard.exiger_admin(request)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as ex:
        f_etat = ex.submit(nextcloud_accounts.etat, commands.occ)
        f_comptes = ex.submit(nextcloud_accounts.comptes, commands.occ)
        f_groupes = ex.submit(nextcloud_accounts.groupes, commands.occ)
        f_dossiers = ex.submit(lambda o: nextcloud_accounts.dossiers(o) if ("groupfolders" in o(["app:list", "--enabled", "--output=json"])[1]) else [], commands.occ)
        e = f_etat.result()
        if not e["present"]:
            return {"etat": e, "rang": j["rang"]}
        try:
            comptes_, groupes_, dossiers_ = f_comptes.result(), f_groupes.result(), f_dossiers.result()
        except nextcloud_accounts.Erreur as err:
            raise HTTPException(500, str(err))
    nextcloud_accounts.nuage_usage_en_fond()
    d = nextcloud_accounts.lire()
    promis = sum(c["quota"] or 0 for c in comptes_ if c["etat"] == "actif")
    attente = sorted(c["id"] for c in comptes_ if c["etat"] == "attente")
    with nextcloud_accounts._verrou:
        d = nextcloud_accounts.lire()
        if d["en_attente_vus"] != attente and set(attente) <= set(d["en_attente_vus"]):
            d["en_attente_vus"] = attente     # validées ou refusées entre-temps (les nouvelles, c'est la surveillance qui les signale)
            nextcloud_accounts.ecrire(d)
    tous_groupes = {g["id"]: g["membres"] for g in groupes_}
    for c in comptes_:
        c["rang_nuage"] = d.get("rang_de", {}).get(c["id"]) if d.get("rangs_actif") else None
    rangs = [{"id": r["id"], "nom": r["nom"], "membres": sum(1 for v in d.get("rang_de", {}).values() if v == r["id"]),
              "voient": len(tous_groupes.get(r["id"], []))} for r in nextcloud_accounts.rangs_de(d)]
    groupes_ = [g for g in groupes_ if not g["id"].startswith(nextcloud_accounts.PREFIXE_RANG)]
    return {"etat": e, "rang": j["rang"], "plafond_go": d["plafond_go"], "comptes": comptes_, "groupes": groupes_,
            "rangs_actif": bool(d.get("rangs_actif")), "rangs": rangs, "createur": j["identifiant"] == guard.nom_hote(),
            "charte": nextcloud_accounts.charte(commands.occ), "charte_defaut": nextcloud_accounts.CHARTE_DEFAUT, "charte_max": nextcloud_accounts.CHARTE_MAX,
            "dossiers": dossiers_, "modes": {k: {"nom": v[1], "detail": v[2]} for k, v in nextcloud_accounts.MODES.items()},
            "tous": nextcloud_accounts.GROUPE_TOUS, "promis": promis, "espace": None if config.DEMO else measurements.espace(["/srv/data"]).get("/srv/data")}


@router.post("/api/nuage/mettre-en-route")
def api_nuage_en_route(request: Request):
    guard.exiger_super(request, fort=True)
    _nuage(nextcloud_accounts.mettre_en_route)
    activity_log.noter("nuage", "Comptes Nextcloud mis en route : inscription avec validation, 5 Go par défaut, dossiers communs", request.state.nom)
    return {"ok": True}


@router.post("/api/nuage/reglages")
async def api_nuage_reglages(request: Request):
    guard.exiger_super(request)
    corps = await request.json()
    d = nextcloud_accounts.lire()
    if "plafond_go" in corps:
        go = corps["plafond_go"]
        if not isinstance(go, (int, float)) or not 1 <= go <= 100000:
            raise HTTPException(400, "Plafond invalide.")
        d["plafond_go"] = go
        nextcloud_accounts.ecrire(d)
        activity_log.noter("nuage", f"Plafond d'espace par compte : {fgo(go)}", request.state.nom)
    if "quota_defaut_go" in corps:
        go = corps["quota_defaut_go"]
        if not isinstance(go, (int, float)) or not 0 < go <= d["plafond_go"]:
            raise HTTPException(400, f"Espace par défaut : entre 0 et {d['plafond_go']} Go (le plafond).")
        _nuage(nextcloud_accounts.quota_defaut, go)
        activity_log.noter("nuage", f"Espace par défaut des nouveaux comptes : {fgo(go)}", request.state.nom)
    return {"ok": True}


@router.post("/api/nuage/charte")
async def api_nuage_charte(request: Request):
    """La charte affichée sur le formulaire d'inscription (super admin)."""
    guard.exiger_super(request)
    texte = (await request.json()).get("texte", "")
    _nuage(nextcloud_accounts.poser_charte, texte)
    activity_log.noter("nuage", "Charte d'inscription " + ("modifiée" if str(texte).strip() else "retirée"), request.state.nom)
    return {"ok": True}


@router.get("/api/nuage/attente")
def api_nuage_attente(request: Request):
    """Nombre d'inscriptions qui attendent (léger : pour la pastille de l'Établi)."""
    guard.exiger_admin(request)
    return {"attente": len(nextcloud_accounts.lire()["en_attente_vus"])}


@router.post("/api/nuage/comptes")
async def api_nuage_ajouter(request: Request):
    """Un compte créé directement par un super admin (pour quelqu'un qui ne passe pas par l'inscription)."""
    guard.exiger_super(request)
    corps = await request.json()
    uid = str(corps.get("id", "")).strip().lower()
    mdp = accounts.provisoire()
    _nuage(nextcloud_accounts.ajouter, uid, str(corps.get("nom", "")).strip()[:60], str(corps.get("email", "")).strip()[:120], mdp)
    texte = poser_rang(uid, corps.get("rang")) if corps.get("rang") else ""
    activity_log.noter("nuage", f"Compte Nextcloud créé : {uid}{texte}", request.state.nom)
    nextcloud_accounts.nuage_usage_en_fond([uid])
    return {"ok": True, "mdp": mdp}


@router.post("/api/nuage/comptes/{uid}")
async def api_nuage_compte(uid: str, request: Request):
    corps = await request.json()
    action = corps.get("action")
    fort = action in ("refuser", "supprimer", "mdp")
    j = guard.exiger_admin(request, "admin" if action in ("quota", "groupe") else "super", fort=fort)  # rang, valider : super
    liste = {c["id"]: c for c in _nuage(nextcloud_accounts.comptes)}
    c = liste.get(uid)
    if not c:
        raise HTTPException(404, "Compte inconnu.")
    if c["admin"] and action in ("suspendre", "supprimer", "refuser"):
        raise HTTPException(403, "C'est un compte administrateur de Nextcloud : à gérer dans Nextcloud.")
    qui = request.state.nom
    if action == "valider":
        _nuage(nextcloud_accounts.valider, uid)
        rid = corps.get("rang")
        texte = poser_rang(uid, rid) if rid else ""
        activity_log.noter("nuage", f"Inscription validée : {c['nom']} ({uid}){texte}", qui)
    elif action == "refuser":
        _nuage(nextcloud_accounts.supprimer, uid)
        activity_log.noter("nuage", f"Inscription refusée : {c['nom']} ({uid}), compte supprimé", qui)
    elif action == "suspendre":
        actif = bool(corps.get("actif"))
        _nuage(nextcloud_accounts.suspendre, uid, actif)
        activity_log.noter("nuage", f"Compte {'réactivé' if actif else 'suspendu'} : {c['nom']} ({uid})", qui)
    elif action == "rang":
        d = nextcloud_accounts.lire()
        avant = nextcloud_accounts.nom_rang(d, d.get("rang_de", {}).get(uid)) or "aucun"
        texte = poser_rang(uid, corps.get("rang"))
        activity_log.noter("nuage", f"Rang de {c['nom']} ({uid}) : {avant} → {nextcloud_accounts.nom_rang(nextcloud_accounts.lire(), corps.get('rang')) or 'aucun'}", qui)
    elif action == "supprimer":
        _nuage(nextcloud_accounts.supprimer, uid)
        d = nextcloud_accounts.lire()
        if d.get("rang_de", {}).pop(uid, None):
            nextcloud_accounts.ecrire(d)
        activity_log.noter("nuage", f"Compte supprimé avec ses fichiers : {c['nom']} ({uid})", qui)
    elif action == "quota":
        go = corps.get("go")
        plafond = nextcloud_accounts.lire()["plafond_go"]
        if go is None:
            if j["rang"] != "super":
                raise HTTPException(403, "Seul un super admin peut donner un espace illimité.")
        elif not isinstance(go, (int, float)) or go <= 0:
            raise HTTPException(400, "Espace invalide.")
        elif go > plafond and j["rang"] != "super":
            raise HTTPException(403, f"Au-delà du plafond ({plafond} Go) : seul un super admin peut le faire.")
        avant = "illimité" if c["illimite"] else fgo(c['quota'] / nextcloud_accounts.GO)
        _nuage(nextcloud_accounts.changer_quota, uid, go)
        activity_log.noter("nuage", f"Espace de {c['nom']} ({uid}) : {avant} → {fgo(go)}", qui)
    elif action == "groupe":
        g, dedans = str(corps.get("groupe", "")), bool(corps.get("dedans"))
        if g.startswith(nextcloud_accounts.PREFIXE_RANG):
            raise HTTPException(400, "Pour les rangs, change le rang de la personne.")
        _nuage(nextcloud_accounts.groupe_membres, g, uid, dedans)
        activity_log.noter("nuage", f"{c['nom']} ({uid}) {'ajouté au' if dedans else 'retiré du'} groupe {g}", qui)
    elif action == "mdp":
        mdp = accounts.provisoire()
        _nuage(nextcloud_accounts.nouveau_mdp, uid, mdp)
        activity_log.noter("nuage", f"Nouveau mot de passe Nextcloud pour {c['nom']} ({uid})", qui)
        return {"ok": True, "mdp": mdp}
    else:
        raise HTTPException(400, "Action inconnue.")
    return {"ok": True}


def nom_groupe_nuage(g):
    if g == nextcloud_accounts.GROUPE_TOUS:
        return "tous les membres"
    r = nextcloud_accounts.nom_rang(nextcloud_accounts.lire(), g)
    return f"rang {r} et au-dessus" if r else g


def poser_rang(uid, rid):
    """Donne le rang `rid` (None : aucun) et met les groupes à jour. Renvoie « , rang X » pour le Journal."""
    with nextcloud_accounts._verrou:
        d = nextcloud_accounts.lire()
        if rid and rid not in [r["id"] for r in nextcloud_accounts.rangs_de(d)]:
            raise HTTPException(400, "Rang inconnu.")
        if rid:
            d.setdefault("rang_de", {})[uid] = rid
        else:
            d.setdefault("rang_de", {}).pop(uid, None)
        nextcloud_accounts.ecrire(d)
    _nuage(nextcloud_accounts.synchroniser, d, {uid})
    return f", rang {nextcloud_accounts.nom_rang(d, rid)}" if rid else ""


@router.post("/api/nuage/rangs")
async def api_nuage_rangs(request: Request):
    """Les rangs : réglés par le compte du serveur seulement (c'est lui qui décide de la hiérarchie)."""
    j = guard.exiger_admin(request, "super")
    if j["identifiant"] != guard.nom_hote():
        raise HTTPException(403, "Seul le compte du serveur règle les rangs.")
    corps = await request.json()
    action, qui = corps.get("action"), request.state.nom
    with nextcloud_accounts._verrou:
        d = nextcloud_accounts.lire()
        ids = [r["id"] for r in nextcloud_accounts.rangs_de(d)]
        rid = corps.get("id")
        if action not in ("activer", "creer") and rid not in ids:
            raise HTTPException(404, "Rang inconnu.")
        tous = False
        if action == "activer":
            d["rangs_actif"] = bool(corps.get("actif"))
            tous = True
            texte = "Rangs activés" if d["rangs_actif"] else "Rangs désactivés (chacun garde son rang, sans effet jusqu'à la réactivation)"
        elif action == "creer":
            nom = str(corps.get("nom", ""))
            _nuage(nextcloud_accounts.creer_rang, d, nom)
            texte = f"Nouveau rang : {nom.strip()}"
        elif action == "renommer":
            nom = str(corps.get("nom", "")).strip()[:40]
            if not nom:
                raise HTTPException(400, "Nom vide.")
            avant = nextcloud_accounts.nom_rang(d, rid)
            next(r for r in d["rangs"] if r["id"] == rid)["nom"] = nom
            commands.occ(["group:add", "--display-name", nom, rid])   # sans effet si le groupe existe ; le nom affiché vient de Jabba
            texte = f"Rang {avant} renommé en {nom}"
        elif action == "deplacer":
            i = ids.index(rid)
            k = i + (1 if corps.get("sens") == "haut" else -1)
            if not 0 <= k < len(ids):
                return {"ok": True}
            d["rangs"][i], d["rangs"][k] = d["rangs"][k], d["rangs"][i]
            tous = True
            texte = "Ordre des rangs : " + " < ".join(r["nom"] for r in d["rangs"])
        elif action == "supprimer":
            nom = nextcloud_accounts.nom_rang(d, rid)
            nextcloud_accounts.supprimer_rang(commands.occ, d, rid)
            tous = True
            texte = f"Rang supprimé : {nom} (ses membres descendent d'un rang)"
        else:
            raise HTTPException(400, "Action inconnue.")
        nextcloud_accounts.ecrire(d)
    if tous:
        _nuage(nextcloud_accounts.synchroniser, d)
    activity_log.noter("nuage", texte, qui)
    return {"ok": True}


@router.post("/api/nuage/groupes")
async def api_nuage_groupes(request: Request):
    guard.exiger_admin(request)
    corps = await request.json()
    nom = str(corps.get("nom", "")).strip()
    if corps.get("supprimer"):
        _nuage(nextcloud_accounts.supprimer_groupe, nom)
        activity_log.noter("nuage", f"Groupe supprimé : {nom}", request.state.nom)
    else:
        _nuage(nextcloud_accounts.nouveau_groupe, nom)
        activity_log.noter("nuage", f"Groupe créé : {nom}", request.state.nom)
    return {"ok": True}


@router.post("/api/nuage/dossiers")
async def api_nuage_dossiers(request: Request):
    corps = await request.json()
    action = corps.get("action")
    guard.exiger_admin(request, fort=action == "supprimer")
    qui = request.state.nom
    fid = corps.get("id")
    noms = {f["id"]: f["nom"] for f in _nuage(nextcloud_accounts.dossiers)}
    if action != "creer" and fid not in noms:
        raise HTTPException(404, "Dossier inconnu.")
    if action == "creer":
        nom = str(corps.get("nom", "")).strip()
        fid = _nuage(nextcloud_accounts.nouveau_dossier, nom)
        groupe, mode = corps.get("groupe") or nextcloud_accounts.GROUPE_TOUS, corps.get("mode") or "lecture"
        if mode not in nextcloud_accounts.MODES:
            raise HTTPException(400, "Mode inconnu.")
        _nuage(nextcloud_accounts.dossier_acces, fid, groupe, mode)
        activity_log.noter("nuage", f"Dossier commun créé : {nom} ({nom_groupe_nuage(groupe)}, {nextcloud_accounts.MODES[mode][1].lower()})", qui)
        return {"ok": True, "id": fid}
    nom = noms[fid]
    if action == "acces":
        groupe, mode = str(corps.get("groupe", "")), corps.get("mode")
        if mode is not None and mode not in nextcloud_accounts.MODES:
            raise HTTPException(400, "Mode inconnu.")
        _nuage(nextcloud_accounts.dossier_acces, fid, groupe, mode)
        activity_log.noter("nuage", f"Dossier {nom} : {nom_groupe_nuage(groupe)} → {'plus d’accès' if mode is None else nextcloud_accounts.MODES[mode][1].lower()}", qui)
    elif action == "quota":
        go = corps.get("go")
        if go is not None and (not isinstance(go, (int, float)) or go <= 0):
            raise HTTPException(400, "Taille invalide.")
        _nuage(nextcloud_accounts.dossier_quota, fid, go)
        activity_log.noter("nuage", f"Dossier {nom} : taille {'illimitée' if go is None else fgo(go) + ' au plus'}", qui)
    elif action == "renommer":
        _nuage(nextcloud_accounts.renommer_dossier, fid, str(corps.get("nom", "")))
        activity_log.noter("nuage", f"Dossier {nom} renommé en {corps.get('nom')}", qui)
    elif action == "supprimer":
        _nuage(nextcloud_accounts.supprimer_dossier, fid)
        activity_log.noter("nuage", f"Dossier commun supprimé avec son contenu : {nom}", qui)
    else:
        raise HTTPException(400, "Action inconnue.")
    return {"ok": True}
