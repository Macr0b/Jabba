"""API des comptes Nextcloud : inscriptions, espace, rangs, groupes et dossiers communs.

Sommaire :
  outils communs : format_gigabytes, run_cloud_action, set_rank
  GET  /api/nuage                    cloud_overview
  POST /api/nuage/mettre-en-route    set_up_cloud
  POST /api/nuage/reglages           cloud_settings
  POST /api/nuage/charte             cloud_charter
  GET  /api/nuage/attente            pending_signups
  POST /api/nuage/comptes            add_cloud_account
  POST /api/nuage/comptes/{uid}      cloud_account_action
  POST /api/nuage/rangs              rank_action
  POST /api/nuage/groupes            group_action
  POST /api/nuage/dossiers           team_folder_action
"""

from fastapi import APIRouter, HTTPException, Request

import config
from host import commands
from security import accounts, guard
from services import activity_log, measurements, nextcloud_accounts

router = APIRouter()


def format_gigabytes(gigabytes):
    """12.5 → « 12,5 Go » ; None → « illimité »."""
    return (
        "illimité"
        if gigabytes is None
        else str(round(float(gigabytes), 1)).removesuffix(".0").replace(".", ",") + " Go"
    )


def run_cloud_action(function, *a):
    try:
        return function(commands.occ, *a)
    except nextcloud_accounts.CloudError as error:
        raise HTTPException(400, str(error))


@router.get("/api/nuage")
def cloud_overview(request: Request):
    session = guard.require_admin(request)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(4) as executor:
        state_future = executor.submit(nextcloud_accounts.overview, commands.occ)
        accounts_future = executor.submit(nextcloud_accounts.accounts, commands.occ)
        groups_future = executor.submit(nextcloud_accounts.groups, commands.occ)
        folders_future = executor.submit(
            lambda entry: (
                nextcloud_accounts.team_folders(entry)
                if ("groupfolders" in entry(["app:list", "--enabled", "--output=json"])[1])
                else []
            ),
            commands.occ,
        )
        state = state_future.result()
        if not state["present"]:
            return {"etat": state, "rang": session["rang"]}
        try:
            accounts_list, groups_list, folders_list = (
                accounts_future.result(),
                groups_future.result(),
                folders_future.result(),
            )
        except nextcloud_accounts.CloudError as error:
            raise HTTPException(500, str(error))
    nextcloud_accounts.refresh_usage_in_background()
    cloud_state = nextcloud_accounts.read()
    promised = sum(account["quota"] or 0 for account in accounts_list if account["etat"] == "actif")
    pending = sorted(account["id"] for account in accounts_list if account["etat"] == "attente")
    with nextcloud_accounts._lock:
        cloud_state = nextcloud_accounts.read()
        if cloud_state["en_attente_vus"] != pending and set(pending) <= set(cloud_state["en_attente_vus"]):
            cloud_state["en_attente_vus"] = (
                pending  # validées ou refusées entre-temps (les nouvelles, c'est la surveillance qui les signale)
            )
            nextcloud_accounts.write(cloud_state)
    all_groups = {group["id"]: group["membres"] for group in groups_list}
    for account in accounts_list:
        account["rang_nuage"] = (
            cloud_state.get("rang_de", {}).get(account["id"]) if cloud_state.get("rangs_actif") else None
        )
    ranks = [
        {
            "id": rank["id"],
            "nom": rank["nom"],
            "membres": sum(1 for value in cloud_state.get("rang_de", {}).values() if value == rank["id"]),
            "voient": len(all_groups.get(rank["id"], [])),
        }
        for rank in nextcloud_accounts.ranks_of(cloud_state)
    ]
    groups_list = [group for group in groups_list if not group["id"].startswith(nextcloud_accounts.RANK_PREFIX)]
    return {
        "etat": state,
        "rang": session["rang"],
        "plafond_go": cloud_state["plafond_go"],
        "comptes": accounts_list,
        "groupes": groups_list,
        "rangs_actif": bool(cloud_state.get("rangs_actif")),
        "rangs": ranks,
        "createur": session["identifiant"] == guard.hostname(),
        "charte": nextcloud_accounts.charter(commands.occ),
        "charte_defaut": nextcloud_accounts.DEFAULT_CHARTER,
        "charte_max": nextcloud_accounts.CHARTER_MAX,
        "dossiers": folders_list,
        "modes": {
            other_index: {"nom": value[1], "detail": value[2]}
            for other_index, value in nextcloud_accounts.MODES.items()
        },
        "tous": nextcloud_accounts.MEMBERS_GROUP,
        "promis": promised,
        "espace": None if config.DEMO else measurements.disk_space(["/srv/data"]).get("/srv/data"),
    }


@router.post("/api/nuage/mettre-en-route")
def set_up_cloud(request: Request):
    guard.require_super_admin(request, strong=True)
    run_cloud_action(nextcloud_accounts.set_up)
    activity_log.record(
        "nuage",
        "Comptes Nextcloud mis en route : inscription avec validation, 5 Go par défaut, dossiers communs",
        request.state.nom,
    )
    return {"ok": True}


@router.post("/api/nuage/reglages")
async def cloud_settings(request: Request):
    guard.require_super_admin(request)
    body = await request.json()
    cloud_state = nextcloud_accounts.read()
    if "plafond_go" in body:
        gigabytes = body["plafond_go"]
        if not isinstance(gigabytes, (int, float)) or not 1 <= gigabytes <= 100000:
            raise HTTPException(400, "Plafond invalide.")
        cloud_state["plafond_go"] = gigabytes
        nextcloud_accounts.write(cloud_state)
        activity_log.record("nuage", f"Plafond d'espace par compte : {format_gigabytes(gigabytes)}", request.state.nom)
    if "quota_defaut_go" in body:
        gigabytes = body["quota_defaut_go"]
        if not isinstance(gigabytes, (int, float)) or not 0 < gigabytes <= cloud_state["plafond_go"]:
            raise HTTPException(400, f"Espace par défaut : entre 0 et {cloud_state['plafond_go']} Go (le plafond).")
        run_cloud_action(nextcloud_accounts.set_default_quota, gigabytes)
        activity_log.record(
            "nuage", f"Espace par défaut des nouveaux comptes : {format_gigabytes(gigabytes)}", request.state.nom
        )
    return {"ok": True}


@router.post("/api/nuage/charte")
async def cloud_charter(request: Request):
    """La charte affichée sur le formulaire d'inscription (super admin)."""
    guard.require_super_admin(request)
    text = (await request.json()).get("texte", "")
    run_cloud_action(nextcloud_accounts.set_charter, text)
    activity_log.record(
        "nuage", "Charte d'inscription " + ("modifiée" if str(text).strip() else "retirée"), request.state.nom
    )
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
    body = await request.json()
    user_id = str(body.get("id", "")).strip().lower()
    password = accounts.temporary_password()
    run_cloud_action(
        nextcloud_accounts.add,
        user_id,
        str(body.get("nom", "")).strip()[:60],
        str(body.get("email", "")).strip()[:120],
        password,
    )
    text = set_rank(user_id, body.get("rang")) if body.get("rang") else ""
    activity_log.record("nuage", f"Compte Nextcloud créé : {user_id}{text}", request.state.nom)
    nextcloud_accounts.refresh_usage_in_background([user_id])
    return {"ok": True, "mdp": password}


@router.post("/api/nuage/comptes/{uid}")
async def cloud_account_action(uid: str, request: Request):
    body = await request.json()
    action = body.get("action")
    strong = action in ("refuser", "supprimer", "mdp")
    session = guard.require_admin(
        request, "admin" if action in ("quota", "groupe") else "super", strong=strong
    )  # rang, valider : super
    items = {account["id"]: account for account in run_cloud_action(nextcloud_accounts.accounts)}
    account = items.get(uid)
    if not account:
        raise HTTPException(404, "Compte inconnu.")
    if account["admin"] and action in ("suspendre", "supprimer", "refuser"):
        raise HTTPException(403, "C'est un compte administrateur de Nextcloud : à gérer dans Nextcloud.")
    who = request.state.nom
    if action == "valider":
        run_cloud_action(nextcloud_accounts.approve, uid)
        rank_id = body.get("rang")
        text = set_rank(uid, rank_id) if rank_id else ""
        activity_log.record("nuage", f"Inscription validée : {account['nom']} ({uid}){text}", who)
    elif action == "refuser":
        run_cloud_action(nextcloud_accounts.delete, uid)
        activity_log.record("nuage", f"Inscription refusée : {account['nom']} ({uid}), compte supprimé", who)
    elif action == "suspendre":
        active = bool(body.get("actif"))
        run_cloud_action(nextcloud_accounts.suspend, uid, active)
        activity_log.record("nuage", f"Compte {'réactivé' if active else 'suspendu'} : {account['nom']} ({uid})", who)
    elif action == "rang":
        cloud_state = nextcloud_accounts.read()
        previous_value = nextcloud_accounts.rank_name(cloud_state, cloud_state.get("rang_de", {}).get(uid)) or "aucun"
        text = set_rank(uid, body.get("rang"))
        activity_log.record(
            "nuage",
            f"Rang de {account['nom']} ({uid}) : {previous_value} → {nextcloud_accounts.rank_name(nextcloud_accounts.read(), body.get('rang')) or 'aucun'}",
            who,
        )
    elif action == "supprimer":
        run_cloud_action(nextcloud_accounts.delete, uid)
        cloud_state = nextcloud_accounts.read()
        if cloud_state.get("rang_de", {}).pop(uid, None):
            nextcloud_accounts.write(cloud_state)
        activity_log.record("nuage", f"Compte supprimé avec ses fichiers : {account['nom']} ({uid})", who)
    elif action == "quota":
        gigabytes = body.get("go")
        limit = nextcloud_accounts.read()["plafond_go"]
        if gigabytes is None:
            if session["rang"] != "super":
                raise HTTPException(403, "Seul un super admin peut donner un espace illimité.")
        elif not isinstance(gigabytes, (int, float)) or gigabytes <= 0:
            raise HTTPException(400, "Espace invalide.")
        elif gigabytes > limit and session["rang"] != "super":
            raise HTTPException(403, f"Au-delà du plafond ({limit} Go) : seul un super admin peut le faire.")
        previous_value = (
            "illimité" if account["illimite"] else format_gigabytes(account["quota"] / nextcloud_accounts.GIGABYTE)
        )
        run_cloud_action(nextcloud_accounts.set_quota, uid, gigabytes)
        activity_log.record(
            "nuage", f"Espace de {account['nom']} ({uid}) : {previous_value} → {format_gigabytes(gigabytes)}", who
        )
    elif action == "groupe":
        group, member = str(body.get("groupe", "")), bool(body.get("dedans"))
        if group.startswith(nextcloud_accounts.RANK_PREFIX):
            raise HTTPException(400, "Pour les rangs, change le rang de la personne.")
        run_cloud_action(nextcloud_accounts.set_group_member, group, uid, member)
        activity_log.record(
            "nuage", f"{account['nom']} ({uid}) {'ajouté au' if member else 'retiré du'} groupe {group}", who
        )
    elif action == "mdp":
        password = accounts.temporary_password()
        run_cloud_action(nextcloud_accounts.set_password, uid, password)
        activity_log.record("nuage", f"Nouveau mot de passe Nextcloud pour {account['nom']} ({uid})", who)
        return {"ok": True, "mdp": password}
    else:
        raise HTTPException(400, "Action inconnue.")
    return {"ok": True}


def set_rank(user_id, rank_id):
    """Donne le rang `rid` (None : aucun) et met les groupes à jour. Renvoie « , rang X » pour le Journal."""
    with nextcloud_accounts._lock:
        cloud_state = nextcloud_accounts.read()
        if rank_id and rank_id not in [rank["id"] for rank in nextcloud_accounts.ranks_of(cloud_state)]:
            raise HTTPException(400, "Rang inconnu.")
        if rank_id:
            cloud_state.setdefault("rang_de", {})[user_id] = rank_id
        else:
            cloud_state.setdefault("rang_de", {}).pop(user_id, None)
        nextcloud_accounts.write(cloud_state)
    run_cloud_action(nextcloud_accounts.sync_ranks, cloud_state, {user_id})
    return f", rang {nextcloud_accounts.rank_name(cloud_state, rank_id)}" if rank_id else ""


@router.post("/api/nuage/rangs")
async def rank_action(request: Request):
    """Les rangs : réglés par le compte du serveur seulement (c'est lui qui décide de la hiérarchie)."""
    session = guard.require_admin(request, "super")
    if session["identifiant"] != guard.hostname():
        raise HTTPException(403, "Seul le compte du serveur règle les rangs.")
    body = await request.json()
    action, who = body.get("action"), request.state.nom
    with nextcloud_accounts._lock:
        cloud_state = nextcloud_accounts.read()
        ids = [rank["id"] for rank in nextcloud_accounts.ranks_of(cloud_state)]
        rank_id = body.get("id")
        if action not in ("activer", "creer") and rank_id not in ids:
            raise HTTPException(404, "Rang inconnu.")
        everyone = False
        if action == "activer":
            cloud_state["rangs_actif"] = bool(body.get("actif"))
            everyone = True
            text = (
                "Rangs activés"
                if cloud_state["rangs_actif"]
                else "Rangs désactivés (chacun garde son rang, sans effet jusqu'à la réactivation)"
            )
        elif action == "creer":
            name = str(body.get("nom", ""))
            run_cloud_action(nextcloud_accounts.create_rank, cloud_state, name)
            text = f"Nouveau rang : {name.strip()}"
        elif action == "renommer":
            name = str(body.get("nom", "")).strip()[:40]
            if not name:
                raise HTTPException(400, "Nom vide.")
            previous_value = nextcloud_accounts.rank_name(cloud_state, rank_id)
            next(rank for rank in cloud_state["rangs"] if rank["id"] == rank_id)["nom"] = name
            commands.occ(
                ["group:add", "--display-name", name, rank_id]
            )  # sans effet si le groupe existe ; le nom affiché vient de Jabba
            text = f"Rang {previous_value} renommé en {name}"
        elif action == "deplacer":
            index = ids.index(rank_id)
            other_index = index + (1 if body.get("sens") == "haut" else -1)
            if not 0 <= other_index < len(ids):
                return {"ok": True}
            cloud_state["rangs"][index], cloud_state["rangs"][other_index] = (
                cloud_state["rangs"][other_index],
                cloud_state["rangs"][index],
            )
            everyone = True
            text = "Ordre des rangs : " + " < ".join(rank["nom"] for rank in cloud_state["rangs"])
        elif action == "supprimer":
            name = nextcloud_accounts.rank_name(cloud_state, rank_id)
            nextcloud_accounts.delete_rank(commands.occ, cloud_state, rank_id)
            everyone = True
            text = f"Rang supprimé : {name} (ses membres descendent d'un rang)"
        else:
            raise HTTPException(400, "Action inconnue.")
        nextcloud_accounts.write(cloud_state)
    if everyone:
        run_cloud_action(nextcloud_accounts.sync_ranks, cloud_state)
    activity_log.record("nuage", text, who)
    return {"ok": True}


@router.post("/api/nuage/groupes")
async def group_action(request: Request):
    guard.require_admin(request)
    body = await request.json()
    name = str(body.get("nom", "")).strip()
    if body.get("supprimer"):
        run_cloud_action(nextcloud_accounts.delete_group, name)
        activity_log.record("nuage", f"Groupe supprimé : {name}", request.state.nom)
    else:
        run_cloud_action(nextcloud_accounts.create_group, name)
        activity_log.record("nuage", f"Groupe créé : {name}", request.state.nom)
    return {"ok": True}


@router.post("/api/nuage/dossiers")
async def team_folder_action(request: Request):
    body = await request.json()
    action = body.get("action")
    guard.require_admin(request, strong=action == "supprimer")
    who = request.state.nom
    folder_id = body.get("id")
    names = {function["id"]: function["nom"] for function in run_cloud_action(nextcloud_accounts.team_folders)}
    if action != "creer" and folder_id not in names:
        raise HTTPException(404, "Dossier inconnu.")
    if action == "creer":
        name = str(body.get("nom", "")).strip()
        folder_id = run_cloud_action(nextcloud_accounts.create_team_folder, name)
        group, mode = body.get("groupe") or nextcloud_accounts.MEMBERS_GROUP, body.get("mode") or "lecture"
        if mode not in nextcloud_accounts.MODES:
            raise HTTPException(400, "Mode inconnu.")
        run_cloud_action(nextcloud_accounts.set_team_folder_access, folder_id, group, mode)
        activity_log.record(
            "nuage",
            f"Dossier commun créé : {name} ({nextcloud_accounts.describe_group(group)}, {nextcloud_accounts.MODES[mode][1].lower()})",
            who,
        )
        return {"ok": True, "id": folder_id}
    name = names[folder_id]
    if action == "acces":
        group, mode = str(body.get("groupe", "")), body.get("mode")
        if mode is not None and mode not in nextcloud_accounts.MODES:
            raise HTTPException(400, "Mode inconnu.")
        run_cloud_action(nextcloud_accounts.set_team_folder_access, folder_id, group, mode)
        activity_log.record(
            "nuage",
            f"Dossier {name} : {nextcloud_accounts.describe_group(group)} → {'plus d’accès' if mode is None else nextcloud_accounts.MODES[mode][1].lower()}",
            who,
        )
    elif action == "quota":
        gigabytes = body.get("go")
        if gigabytes is not None and (not isinstance(gigabytes, (int, float)) or gigabytes <= 0):
            raise HTTPException(400, "Taille invalide.")
        run_cloud_action(nextcloud_accounts.set_team_folder_quota, folder_id, gigabytes)
        activity_log.record(
            "nuage",
            f"Dossier {name} : taille {'illimitée' if gigabytes is None else format_gigabytes(gigabytes) + ' au plus'}",
            who,
        )
    elif action == "renommer":
        run_cloud_action(nextcloud_accounts.rename_team_folder, folder_id, str(body.get("nom", "")))
        activity_log.record("nuage", f"Dossier {name} renommé en {body.get('nom')}", who)
    elif action == "supprimer":
        run_cloud_action(nextcloud_accounts.delete_team_folder, folder_id)
        activity_log.record("nuage", f"Dossier commun supprimé avec son contenu : {name}", who)
    else:
        raise HTTPException(400, "Action inconnue.")
    return {"ok": True}
