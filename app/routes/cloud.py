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

def format_gigabytes(go):
    """12.5 → « 12,5 Go » ; None → « illimité »."""
    return "illimité" if go is None else str(round(float(go), 1)).removesuffix(".0").replace(".", ",") + " Go"


def run_cloud_action(f, *a):
    try:
        return f(commands.occ, *a)
    except nextcloud_accounts.CloudError as e:
        raise HTTPException(400, str(e))


@router.get("/api/nuage")
def cloud_overview(request: Request):
    j = guard.require_admin(request)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as ex:
        f_etat = ex.submit(nextcloud_accounts.overview, commands.occ)
        f_comptes = ex.submit(nextcloud_accounts.accounts, commands.occ)
        f_groupes = ex.submit(nextcloud_accounts.groups, commands.occ)
        f_dossiers = ex.submit(lambda o: nextcloud_accounts.team_folders(o) if ("groupfolders" in o(["app:list", "--enabled", "--output=json"])[1]) else [], commands.occ)
        e = f_etat.result()
        if not e["present"]:
            return {"etat": e, "rang": j["rang"]}
        try:
            comptes_, groupes_, dossiers_ = f_comptes.result(), f_groupes.result(), f_dossiers.result()
        except nextcloud_accounts.CloudError as err:
            raise HTTPException(500, str(err))
    nextcloud_accounts.refresh_usage_in_background()
    d = nextcloud_accounts.read()
    promis = sum(c["quota"] or 0 for c in comptes_ if c["etat"] == "actif")
    attente = sorted(c["id"] for c in comptes_ if c["etat"] == "attente")
    with nextcloud_accounts._lock:
        d = nextcloud_accounts.read()
        if d["en_attente_vus"] != attente and set(attente) <= set(d["en_attente_vus"]):
            d["en_attente_vus"] = attente     # validées ou refusées entre-temps (les nouvelles, c'est la surveillance qui les signale)
            nextcloud_accounts.write(d)
    tous_groupes = {g["id"]: g["membres"] for g in groupes_}
    for c in comptes_:
        c["rang_nuage"] = d.get("rang_de", {}).get(c["id"]) if d.get("rangs_actif") else None
    rangs = [{"id": r["id"], "nom": r["nom"], "membres": sum(1 for v in d.get("rang_de", {}).values() if v == r["id"]),
              "voient": len(tous_groupes.get(r["id"], []))} for r in nextcloud_accounts.ranks_of(d)]
    groupes_ = [g for g in groupes_ if not g["id"].startswith(nextcloud_accounts.RANK_PREFIX)]
    return {"etat": e, "rang": j["rang"], "plafond_go": d["plafond_go"], "comptes": comptes_, "groupes": groupes_,
            "rangs_actif": bool(d.get("rangs_actif")), "rangs": rangs, "createur": j["identifiant"] == guard.hostname(),
            "charte": nextcloud_accounts.charter(commands.occ), "charte_defaut": nextcloud_accounts.DEFAULT_CHARTER, "charte_max": nextcloud_accounts.CHARTER_MAX,
            "dossiers": dossiers_, "modes": {k: {"nom": v[1], "detail": v[2]} for k, v in nextcloud_accounts.MODES.items()},
            "tous": nextcloud_accounts.MEMBERS_GROUP, "promis": promis, "espace": None if config.DEMO else measurements.disk_space(["/srv/data"]).get("/srv/data")}


@router.post("/api/nuage/mettre-en-route")
def set_up_cloud(request: Request):
    guard.require_super_admin(request, fort=True)
    run_cloud_action(nextcloud_accounts.set_up)
    activity_log.record("nuage", "Comptes Nextcloud mis en route : inscription avec validation, 5 Go par défaut, dossiers communs", request.state.nom)
    return {"ok": True}


@router.post("/api/nuage/reglages")
async def cloud_settings(request: Request):
    guard.require_super_admin(request)
    corps = await request.json()
    d = nextcloud_accounts.read()
    if "plafond_go" in corps:
        go = corps["plafond_go"]
        if not isinstance(go, (int, float)) or not 1 <= go <= 100000:
            raise HTTPException(400, "Plafond invalide.")
        d["plafond_go"] = go
        nextcloud_accounts.write(d)
        activity_log.record("nuage", f"Plafond d'espace par compte : {format_gigabytes(go)}", request.state.nom)
    if "quota_defaut_go" in corps:
        go = corps["quota_defaut_go"]
        if not isinstance(go, (int, float)) or not 0 < go <= d["plafond_go"]:
            raise HTTPException(400, f"Espace par défaut : entre 0 et {d['plafond_go']} Go (le plafond).")
        run_cloud_action(nextcloud_accounts.set_default_quota, go)
        activity_log.record("nuage", f"Espace par défaut des nouveaux comptes : {format_gigabytes(go)}", request.state.nom)
    return {"ok": True}


@router.post("/api/nuage/charte")
async def cloud_charter(request: Request):
    """La charte affichée sur le formulaire d'inscription (super admin)."""
    guard.require_super_admin(request)
    texte = (await request.json()).get("texte", "")
    run_cloud_action(nextcloud_accounts.set_charter, texte)
    activity_log.record("nuage", "Charte d'inscription " + ("modifiée" if str(texte).strip() else "retirée"), request.state.nom)
    return {"ok": True}


@router.get("/api/nuage/attente")
def pending_signups(request: Request):
    """Nombre d'inscriptions qui attendent (léger : pour la pastille de l'Établi)."""
    guard.require_admin(request)
    return {"attente": len(nextcloud_accounts.read()["en_attente_vus"])}


@router.post("/api/nuage/comptes")
async def add_cloud_account(request: Request):
    """Un compte créé directement par un super admin (pour quelqu'un qui ne passe pas par l'inscription)."""
    guard.require_super_admin(request)
    corps = await request.json()
    uid = str(corps.get("id", "")).strip().lower()
    mdp = accounts.temporary_password()
    run_cloud_action(nextcloud_accounts.add, uid, str(corps.get("nom", "")).strip()[:60], str(corps.get("email", "")).strip()[:120], mdp)
    texte = set_rank(uid, corps.get("rang")) if corps.get("rang") else ""
    activity_log.record("nuage", f"Compte Nextcloud créé : {uid}{texte}", request.state.nom)
    nextcloud_accounts.refresh_usage_in_background([uid])
    return {"ok": True, "mdp": mdp}


@router.post("/api/nuage/comptes/{uid}")
async def cloud_account_action(uid: str, request: Request):
    corps = await request.json()
    action = corps.get("action")
    fort = action in ("refuser", "supprimer", "mdp")
    j = guard.require_admin(request, "admin" if action in ("quota", "groupe") else "super", fort=fort)  # rang, valider : super
    liste = {c["id"]: c for c in run_cloud_action(nextcloud_accounts.accounts)}
    c = liste.get(uid)
    if not c:
        raise HTTPException(404, "Compte inconnu.")
    if c["admin"] and action in ("suspendre", "supprimer", "refuser"):
        raise HTTPException(403, "C'est un compte administrateur de Nextcloud : à gérer dans Nextcloud.")
    qui = request.state.nom
    if action == "valider":
        run_cloud_action(nextcloud_accounts.approve, uid)
        rid = corps.get("rang")
        texte = set_rank(uid, rid) if rid else ""
        activity_log.record("nuage", f"Inscription validée : {c['nom']} ({uid}){texte}", qui)
    elif action == "refuser":
        run_cloud_action(nextcloud_accounts.delete, uid)
        activity_log.record("nuage", f"Inscription refusée : {c['nom']} ({uid}), compte supprimé", qui)
    elif action == "suspendre":
        actif = bool(corps.get("actif"))
        run_cloud_action(nextcloud_accounts.suspend, uid, actif)
        activity_log.record("nuage", f"Compte {'réactivé' if actif else 'suspendu'} : {c['nom']} ({uid})", qui)
    elif action == "rang":
        d = nextcloud_accounts.read()
        avant = nextcloud_accounts.rank_name(d, d.get("rang_de", {}).get(uid)) or "aucun"
        texte = set_rank(uid, corps.get("rang"))
        activity_log.record("nuage", f"Rang de {c['nom']} ({uid}) : {avant} → {nextcloud_accounts.rank_name(nextcloud_accounts.read(), corps.get('rang')) or 'aucun'}", qui)
    elif action == "supprimer":
        run_cloud_action(nextcloud_accounts.delete, uid)
        d = nextcloud_accounts.read()
        if d.get("rang_de", {}).pop(uid, None):
            nextcloud_accounts.write(d)
        activity_log.record("nuage", f"Compte supprimé avec ses fichiers : {c['nom']} ({uid})", qui)
    elif action == "quota":
        go = corps.get("go")
        plafond = nextcloud_accounts.read()["plafond_go"]
        if go is None:
            if j["rang"] != "super":
                raise HTTPException(403, "Seul un super admin peut donner un espace illimité.")
        elif not isinstance(go, (int, float)) or go <= 0:
            raise HTTPException(400, "Espace invalide.")
        elif go > plafond and j["rang"] != "super":
            raise HTTPException(403, f"Au-delà du plafond ({plafond} Go) : seul un super admin peut le faire.")
        avant = "illimité" if c["illimite"] else format_gigabytes(c['quota'] / nextcloud_accounts.GIGABYTE)
        run_cloud_action(nextcloud_accounts.set_quota, uid, go)
        activity_log.record("nuage", f"Espace de {c['nom']} ({uid}) : {avant} → {format_gigabytes(go)}", qui)
    elif action == "groupe":
        g, dedans = str(corps.get("groupe", "")), bool(corps.get("dedans"))
        if g.startswith(nextcloud_accounts.RANK_PREFIX):
            raise HTTPException(400, "Pour les rangs, change le rang de la personne.")
        run_cloud_action(nextcloud_accounts.set_group_member, g, uid, dedans)
        activity_log.record("nuage", f"{c['nom']} ({uid}) {'ajouté au' if dedans else 'retiré du'} groupe {g}", qui)
    elif action == "mdp":
        mdp = accounts.temporary_password()
        run_cloud_action(nextcloud_accounts.set_password, uid, mdp)
        activity_log.record("nuage", f"Nouveau mot de passe Nextcloud pour {c['nom']} ({uid})", qui)
        return {"ok": True, "mdp": mdp}
    else:
        raise HTTPException(400, "Action inconnue.")
    return {"ok": True}


def cloud_group_name(g):
    if g == nextcloud_accounts.MEMBERS_GROUP:
        return "tous les membres"
    r = nextcloud_accounts.rank_name(nextcloud_accounts.read(), g)
    return f"rang {r} et au-dessus" if r else g


def set_rank(uid, rid):
    """Donne le rang `rid` (None : aucun) et met les groupes à jour. Renvoie « , rang X » pour le Journal."""
    with nextcloud_accounts._lock:
        d = nextcloud_accounts.read()
        if rid and rid not in [r["id"] for r in nextcloud_accounts.ranks_of(d)]:
            raise HTTPException(400, "Rang inconnu.")
        if rid:
            d.setdefault("rang_de", {})[uid] = rid
        else:
            d.setdefault("rang_de", {}).pop(uid, None)
        nextcloud_accounts.write(d)
    run_cloud_action(nextcloud_accounts.sync_ranks, d, {uid})
    return f", rang {nextcloud_accounts.rank_name(d, rid)}" if rid else ""


@router.post("/api/nuage/rangs")
async def rank_action(request: Request):
    """Les rangs : réglés par le compte du serveur seulement (c'est lui qui décide de la hiérarchie)."""
    j = guard.require_admin(request, "super")
    if j["identifiant"] != guard.hostname():
        raise HTTPException(403, "Seul le compte du serveur règle les rangs.")
    corps = await request.json()
    action, qui = corps.get("action"), request.state.nom
    with nextcloud_accounts._lock:
        d = nextcloud_accounts.read()
        ids = [r["id"] for r in nextcloud_accounts.ranks_of(d)]
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
            run_cloud_action(nextcloud_accounts.create_rank, d, nom)
            texte = f"Nouveau rang : {nom.strip()}"
        elif action == "renommer":
            nom = str(corps.get("nom", "")).strip()[:40]
            if not nom:
                raise HTTPException(400, "Nom vide.")
            avant = nextcloud_accounts.rank_name(d, rid)
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
            nom = nextcloud_accounts.rank_name(d, rid)
            nextcloud_accounts.delete_rank(commands.occ, d, rid)
            tous = True
            texte = f"Rang supprimé : {nom} (ses membres descendent d'un rang)"
        else:
            raise HTTPException(400, "Action inconnue.")
        nextcloud_accounts.write(d)
    if tous:
        run_cloud_action(nextcloud_accounts.sync_ranks, d)
    activity_log.record("nuage", texte, qui)
    return {"ok": True}


@router.post("/api/nuage/groupes")
async def group_action(request: Request):
    guard.require_admin(request)
    corps = await request.json()
    nom = str(corps.get("nom", "")).strip()
    if corps.get("supprimer"):
        run_cloud_action(nextcloud_accounts.delete_group, nom)
        activity_log.record("nuage", f"Groupe supprimé : {nom}", request.state.nom)
    else:
        run_cloud_action(nextcloud_accounts.create_group, nom)
        activity_log.record("nuage", f"Groupe créé : {nom}", request.state.nom)
    return {"ok": True}


@router.post("/api/nuage/dossiers")
async def team_folder_action(request: Request):
    corps = await request.json()
    action = corps.get("action")
    guard.require_admin(request, fort=action == "supprimer")
    qui = request.state.nom
    fid = corps.get("id")
    noms = {f["id"]: f["nom"] for f in run_cloud_action(nextcloud_accounts.team_folders)}
    if action != "creer" and fid not in noms:
        raise HTTPException(404, "Dossier inconnu.")
    if action == "creer":
        nom = str(corps.get("nom", "")).strip()
        fid = run_cloud_action(nextcloud_accounts.create_team_folder, nom)
        groupe, mode = corps.get("groupe") or nextcloud_accounts.MEMBERS_GROUP, corps.get("mode") or "lecture"
        if mode not in nextcloud_accounts.MODES:
            raise HTTPException(400, "Mode inconnu.")
        run_cloud_action(nextcloud_accounts.set_team_folder_access, fid, groupe, mode)
        activity_log.record("nuage", f"Dossier commun créé : {nom} ({cloud_group_name(groupe)}, {nextcloud_accounts.MODES[mode][1].lower()})", qui)
        return {"ok": True, "id": fid}
    nom = noms[fid]
    if action == "acces":
        groupe, mode = str(corps.get("groupe", "")), corps.get("mode")
        if mode is not None and mode not in nextcloud_accounts.MODES:
            raise HTTPException(400, "Mode inconnu.")
        run_cloud_action(nextcloud_accounts.set_team_folder_access, fid, groupe, mode)
        activity_log.record("nuage", f"Dossier {nom} : {cloud_group_name(groupe)} → {'plus d’accès' if mode is None else nextcloud_accounts.MODES[mode][1].lower()}", qui)
    elif action == "quota":
        go = corps.get("go")
        if go is not None and (not isinstance(go, (int, float)) or go <= 0):
            raise HTTPException(400, "Taille invalide.")
        run_cloud_action(nextcloud_accounts.set_team_folder_quota, fid, go)
        activity_log.record("nuage", f"Dossier {nom} : taille {'illimitée' if go is None else format_gigabytes(go) + ' au plus'}", qui)
    elif action == "renommer":
        run_cloud_action(nextcloud_accounts.rename_team_folder, fid, str(corps.get("nom", "")))
        activity_log.record("nuage", f"Dossier {nom} renommé en {corps.get('nom')}", qui)
    elif action == "supprimer":
        run_cloud_action(nextcloud_accounts.delete_team_folder, fid)
        activity_log.record("nuage", f"Dossier commun supprimé avec son contenu : {nom}", qui)
    else:
        raise HTTPException(400, "Action inconnue.")
    return {"ok": True}
