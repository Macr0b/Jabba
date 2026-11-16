"""Comptes Nextcloud, gérés depuis Jabba (façon Dropbox).

- Inscription libre (appli Nextcloud « Registration ») : le compte est créé
  désactivé et attend la validation d'un super admin dans Jabba.
- Chaque compte a un espace (quota) : 5 Go par défaut. Les admins le changent,
  sans dépasser le plafond fixé par le super admin. Tout est noté au Journal.
- Dossiers communs (appli Nextcloud « Team folders », ex-« Group folders ») :
  visibles par tous les membres ou par un groupe, en lecture seule, en dépôt
  (ajouter et modifier, sans supprimer) ou en accès complet.

Tout passe par `occ`, la commande d'administration de Nextcloud. Ce module ne
connaît que la fonction `occ(args, input_text=None, timeout=60) -> (code, sortie, erreur)`.
"""

import json
import os
import re
import threading
import time

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
STATE_FILE = os.path.join(DATA_DIR, "nuage.json")
MEMBERS_GROUP = "membres"
APPS = ("registration", "groupfolders")
MODES = {  # droits Nextcloud : lire 1, modifier 2, créer 4, supprimer 8, partager 16
    "lecture": (["read"], "Lecture seule", "Voir et télécharger."),
    "depot": (["read", "write"], "Dépôt", "Voir, ajouter et modifier, sans rien supprimer."),
    "complet": (["read", "write", "share", "delete"], "Accès complet", "Tout faire, y compris supprimer et partager."),
}
MODES_BY_PERMISSIONS = {1: "lecture", 7: "depot", 31: "complet", 15: "complet", 23: "depot"}
GIGABYTE = 1024**3
# Charte affichée sur le formulaire d'inscription (modifiable dans Jabba).
CHARTER_MAX = 1500
DEFAULT_CHARTER = (
    "Ce service est hébergé par un particulier, sur son propre serveur, et chaque compte est validé à la main. "
    "L'administrateur peut techniquement accéder aux fichiers stockés ici : il s'engage à ne pas le faire, "
    "sauf à ta demande ou par obligation légale. Pour des fichiers vraiment privés, chiffre-les toi-même avec "
    "Cryptomator (cryptomator.org) : personne d'autre ne pourra les lire, l'administrateur non plus. "
    "Tout est sauvegardé chaque nuit, mais garde ta propre copie de ce qui compte. "
    "Tu peux demander à tout moment la suppression de ton compte et de tes fichiers. "
    "En créant ton compte, tu acceptes cette charte."
)
_lock = threading.RLock()
USAGE_CACHE = {"t": 0, "v": {}}  # identifiant -> octets utilisés (lu à part : une commande par compte)


class CloudError(Exception):
    pass


# ---------------------------------------------------------------- réglages de Jabba
def read():
    try:
        with open(STATE_FILE) as folder:
            state = json.load(folder)
    except (OSError, ValueError):
        state = {}
    state.setdefault("plafond_go", 50)
    state.setdefault("en_attente_vus", [])  # inscriptions déjà signalées
    return state


def write(state):
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        temporary_file = STATE_FILE + ".tmp"
        with open(temporary_file, "w") as folder:
            json.dump(state, folder, ensure_ascii=False)
        os.replace(temporary_file, STATE_FILE)


# ---------------------------------------------------------------- tailles
def to_bytes(quantity):
    """« 5 GB », « 500 MB », « none » (illimité) → octets, ou None si illimité."""
    quantity = str(quantity or "").strip()
    if quantity in ("", "none", "default"):
        return None
    match = re.fullmatch(r"([\d.]+)\s*([KMGT]?)i?B?", quantity, re.I)
    if not match:
        return None
    return int(float(match.group(1)) * 1024 ** "_KMGT".index((match.group(2) or "_").upper()))


def quota_text(gigabytes):
    """Nombre de Go → texte pour Nextcloud (« 5 GB »), ou « none » si illimité."""
    if gigabytes is None:
        return "none"
    gigabytes = float(gigabytes)
    return f"{int(gigabytes)} GB" if gigabytes == int(gigabytes) else f"{int(gigabytes * 1024)} MB"


# ---------------------------------------------------------------- lecture
def _occ_json(occ, args, timeout=60):
    code, output, error_output = occ(args + ["--output=json"], timeout=timeout)
    if code != 0:
        raise CloudError((error_output or output).strip()[-300:] or "Nextcloud ne répond pas.")
    try:
        return json.loads(output or "null")
    except ValueError:
        raise CloudError("Réponse de Nextcloud illisible.")


def overview(occ):
    """Nextcloud joignable ? Applis installées ? Réglages en place ?"""
    code, output, _ = occ(["status", "--output=json"], timeout=30)
    if code != 0:
        return {"present": False}
    try:
        status = json.loads(output)
    except ValueError:
        return {"present": False}
    apps = _occ_json(occ, ["app:list"]).get("enabled", {})
    settings = (
        {
            key: occ(["config:app:get", "registration", key])[1].strip()
            for key in ("admin_approval_required", "registered_user_group")
        }
        if "registration" in apps
        else {}
    )
    default = occ(["config:app:get", "files", "default_quota"])[1].strip() or "none"
    return {
        "present": True,
        "version": status.get("versionstring"),
        "applis": {app: app in apps for app in APPS},
        "pret": all(app in apps for app in APPS) and settings.get("admin_approval_required") == "yes",
        "quota_defaut": default,
    }


def accounts(occ):
    """Tous les comptes, avec leur état (actif, en attente, suspendu), groupes et espace."""
    raw = _occ_json(occ, ["user:list", "--info", "--limit=0"])
    result = []
    for user_id, user in (raw or {}).items():
        pending = False
        if not user.get("enabled"):
            code, output, _ = occ(["user:setting", user_id, "registration", "send_welcome_mail_on_enable"])
            pending = code == 0 and output.strip() == "yes"
        quota = to_bytes(user.get("quota"))
        result.append(
            {
                "id": user_id,
                "nom": user.get("display_name") or user_id,
                "email": user.get("email") or "",
                "etat": "actif" if user.get("enabled") else ("attente" if pending else "suspendu"),
                "groupes": user.get("groups") or [],
                "admin": "admin" in (user.get("groups") or []),
                "quota": quota,
                "illimite": quota is None,
                "quota_texte": user.get("quota"),
                "utilise": USAGE_CACHE["v"].get(user_id),
                "vu": None if user.get("last_seen") in (None, "never") else user.get("last_seen"),
                "cree": None if user.get("first_seen") in (None, "never") else user.get("first_seen"),
            }
        )
    result.sort(key=lambda item: ({"attente": 0, "actif": 1, "suspendu": 2}[item["etat"]], item["nom"].lower()))
    return result


def refresh_usage(occ, user_ids=None):
    """L'espace utilisé par chaque compte (une commande par compte : fait à part, en fond)."""
    if user_ids is None:
        user_ids = list((_occ_json(occ, ["user:list", "--limit=0"]) or {}).keys())
    for user_id in user_ids:
        code, output, _ = occ(["user:info", user_id, "--output=json"])
        if code == 0:
            try:
                USAGE_CACHE["v"][user_id] = ((json.loads(output).get("storage") or {}).get("used")) or 0
            except ValueError:
                pass
    USAGE_CACHE["t"] = time.time()


def groups(occ):
    raw = _occ_json(occ, ["group:list", "--limit=0"]) or {}
    return [{"id": group, "membres": match} for group, match in sorted(raw.items()) if group != "admin"]


def team_folders(occ):
    raw = _occ_json(occ, ["groupfolders:list"]) or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    result = []
    for folder in raw:
        access = folder.get("groups_list") or {
            group: value.get("permissions") if isinstance(value, dict) else value
            for group, value in (folder.get("groups") or {}).items()
        }
        result.append(
            {
                "id": folder["id"],
                "nom": folder.get("mountPoint") or folder.get("mount_point"),
                "quota": None if (folder.get("quota") or -3) < 0 else folder["quota"],
                "taille": folder.get("size") or 0,
                "acces": [
                    {
                        "groupe": group,
                        "mode": MODES_BY_PERMISSIONS.get(
                            int(permissions),
                            "complet" if int(permissions) & 8 else "depot" if int(permissions) & 4 else "lecture",
                        ),
                    }
                    for group, permissions in sorted(access.items())
                ],
            }
        )
    result.sort(key=lambda folder: folder["nom"].lower())
    return result


# ---------------------------------------------------------------- actions
def _occ_or_fail(occ, args, input_text=None, timeout=60):
    code, output, error_output = occ(args, input_text=input_text, timeout=timeout)
    if code != 0:
        raise CloudError((error_output or output).strip()[-300:] or "Nextcloud a refusé.")
    return output


def set_up(occ, default_quota_gb=5):
    """Installe les deux applis Nextcloud et pose les réglages : inscription validée par un admin, membres, 5 Go."""
    for app in APPS:
        code, _, _ = occ(["app:enable", app], timeout=120)
        if code != 0:
            _occ_or_fail(occ, ["app:install", app], timeout=600)
    code, _, _ = occ(["group:info", MEMBERS_GROUP])
    if code != 0:
        _occ_or_fail(occ, ["group:add", "--display-name", "Tous les membres", MEMBERS_GROUP])
    settings = {
        "admin_approval_required": "yes",  # le compte attend la validation
        "registered_user_group": MEMBERS_GROUP,  # et rejoint « Tous les membres »
        "show_fullname": "yes",
        "enforce_fullname": "yes",
        # Pas encore de serveur d'e-mails : on ne vérifie pas l'adresse (à activer avec le domaine).
        "disable_email_verification": "yes",
    }
    for key, value in settings.items():
        _occ_or_fail(occ, ["config:app:set", "registration", key, "--value", value])
    _occ_or_fail(occ, ["config:app:set", "files", "default_quota", "--value", quota_text(default_quota_gb)])
    if not charter(occ):
        set_charter(occ, DEFAULT_CHARTER)
    reserve_disks_for_admins(occ)


def charter(occ):
    code, output, _ = occ(["config:app:get", "registration", "additional_hint"])
    return output.strip() if code == 0 else ""


def set_charter(occ, text):
    text = " ".join(str(text or "").split())  # une seule ligne : le formulaire n'affiche pas les retours
    if len(text) > CHARTER_MAX:
        raise CloudError(f"Charte trop longue ({CHARTER_MAX} caractères au plus).")
    if text:
        _occ_or_fail(occ, ["config:app:set", "registration", "additional_hint", "--value", text])
    else:
        occ(["config:app:delete", "registration", "additional_hint"])


def set_default_quota(occ, gigabytes):
    _occ_or_fail(occ, ["config:app:set", "files", "default_quota", "--value", quota_text(gigabytes)])


def reserve_disks_for_admins(occ):
    """Les disques branchés au serveur (stockages externes de Nextcloud) sont réservés aux admins.

    Un stockage externe sans personne de désignée est vu par TOUS les comptes : avant qu'un
    nouveau compte puisse entrer, chaque disque encore ouvert à tous est réservé au groupe admin.
    Un disque déjà réservé à des personnes ou des groupes précis n'est pas touché."""
    code, output, _ = occ(["files_external:list", "--output=json"])
    if code != 0:
        return []  # appli des stockages externes absente : aucun disque dans Nextcloud
    try:
        mounts = json.loads(output)
    except ValueError:
        raise CloudError("Nextcloud : liste des disques illisible.")
    done = []
    for match in mounts if isinstance(mounts, list) else []:
        if not match.get("applicable_users") and not match.get("applicable_groups"):
            _occ_or_fail(occ, ["files_external:applicable", "--add-group=admin", str(match["mount_id"])])
            done.append(str(match.get("mount_point", "")).strip("/"))
    return done


def approve(occ, user_id):
    reserve_disks_for_admins(occ)
    _occ_or_fail(occ, ["user:enable", user_id])
    occ(["group:adduser", MEMBERS_GROUP, user_id])


def suspend(occ, user_id, active):
    _occ_or_fail(occ, ["user:enable" if active else "user:disable", user_id])


def delete(occ, user_id):
    _occ_or_fail(occ, ["user:delete", user_id], timeout=600)
    USAGE_CACHE["v"].pop(user_id, None)


def set_quota(occ, user_id, gigabytes):
    _occ_or_fail(occ, ["user:setting", user_id, "files", "quota", quota_text(gigabytes)])


def add(occ, user_id, name, email, password, gigabytes=None):
    """Compte créé par un admin (pas d'inscription) : actif tout de suite, membre de « Tous »."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,31}", user_id):
        raise CloudError("Identifiant : 2 à 32 caractères, lettres minuscules, chiffres, point, tiret.")
    reserve_disks_for_admins(occ)
    args = ["user:add", "--password-from-env", "--group", MEMBERS_GROUP]
    if name:
        args += ["--display-name", name]
    if email:
        args += ["--email", email]
    _occ_with_password(occ, args + [user_id], password)
    if gigabytes is not None:
        set_quota(occ, user_id, gigabytes)


def set_password(occ, user_id, password):
    _occ_with_password(occ, ["user:resetpassword", "--password-from-env", user_id], password)


def _occ_with_password(occ, args, password):
    # Le mot de passe passe par l'entrée standard, jamais dans la ligne de commande.
    code, output, error_output = occ(args, input_text=password, is_password=True, timeout=60)
    if code != 0:
        raise CloudError((error_output or output).strip()[-300:] or "Nextcloud a refusé.")


def set_group_member(occ, group, user_id, member):
    _occ_or_fail(occ, ["group:adduser" if member else "group:removeuser", group, user_id])


def create_group(occ, name):
    if not re.fullmatch(r"[\w .-]{2,40}", name) or name.lower() == "admin" or name.lower().startswith(RANK_PREFIX):
        raise CloudError("Nom de groupe : 2 à 40 lettres, chiffres, espaces ou tirets.")
    _occ_or_fail(occ, ["group:add", name])


def delete_group(occ, name):
    if name in ("admin", MEMBERS_GROUP) or name.startswith(RANK_PREFIX):
        raise CloudError("Ce groupe ne se supprime pas.")
    _occ_or_fail(occ, ["group:delete", name])


def create_team_folder(occ, name):
    name = name.strip()
    if not name or "/" in name or len(name) > 60:
        raise CloudError("Nom de dossier invalide.")
    return int(_occ_or_fail(occ, ["groupfolders:create", name]).strip().splitlines()[-1])


def set_team_folder_access(occ, folder_id, group, mode):
    """mode : lecture, depot, complet ; ou None pour retirer l'accès au groupe."""
    if mode is None:
        _occ_or_fail(occ, ["groupfolders:group", "--delete", str(folder_id), group])
    else:
        _occ_or_fail(occ, ["groupfolders:group", str(folder_id), group] + MODES[mode][0])


def set_team_folder_quota(occ, folder_id, gigabytes):
    _occ_or_fail(
        occ, ["groupfolders:quota", str(folder_id), "unlimited" if gigabytes is None else quota_text(gigabytes)]
    )


def rename_team_folder(occ, folder_id, name):
    if not name.strip() or "/" in name:
        raise CloudError("Nom de dossier invalide.")
    _occ_or_fail(occ, ["groupfolders:rename", str(folder_id), name.strip()])


def delete_team_folder(occ, folder_id):
    _occ_or_fail(occ, ["groupfolders:delete", "--force", str(folder_id)], timeout=600)


# ---------------------------------------------------------------- rangs
# Option : des rangs rangés du plus bas au plus haut (Adhérent < Employé < Gérant…),
# nommés librement par le compte du serveur. Chaque rang est un groupe Nextcloud ;
# une personne fait partie du groupe de son rang ET de ceux en dessous. Un dossier
# commun donné à un rang est donc vu par ce rang et ceux au-dessus, pas en dessous ;
# et si plusieurs rangs y ont accès, chacun garde le meilleur accès qui le concerne.
RANK_PREFIX = "rang-"


def ranks_of(state):
    """Les rangs, du plus bas au plus haut : [{id, nom}]."""
    return state.setdefault("rangs", [])


def rank_name(state, rank_id):
    return next((rank["nom"] for rank in ranks_of(state) if rank["id"] == rank_id), None)


def wanted_groups(state, user_id):
    """Groupes de rang dont `uid` doit faire partie (son rang et ceux en dessous)."""
    if not state.get("rangs_actif"):
        return set()
    rank_id = state.setdefault("rang_de", {}).get(user_id)
    ids = [rank["id"] for rank in ranks_of(state)]
    if rank_id not in ids:
        return set()
    return set(ids[: ids.index(rank_id) + 1])


def sync_ranks(occ, state, user_ids=None):
    """Met les groupes Nextcloud en accord avec les rangs (après un changement de rang, d'ordre, ou l'option)."""
    members = _occ_json(occ, ["group:list", "--limit=0"]) or {}
    all_ranks = {group for group in members if group.startswith(RANK_PREFIX)}
    if user_ids is None:
        user_ids = set(state.get("rang_de", {})) | {user for group in all_ranks for user in members.get(group, [])}
    for user_id in user_ids:
        wanted = wanted_groups(state, user_id)
        for group in all_ranks | wanted:
            member = user_id in members.get(group, [])
            if group in wanted and not member:
                occ(["group:adduser", group, user_id])
            elif group not in wanted and member:
                occ(["group:removeuser", group, user_id])


def create_rank(occ, state, name):
    import secrets

    name = name.strip()
    if not name or len(name) > 40:
        raise CloudError("Nom de rang : 1 à 40 caractères.")
    if any(rank["nom"].lower() == name.lower() for rank in ranks_of(state)):
        raise CloudError("Ce rang existe déjà.")
    rank_id = RANK_PREFIX + secrets.token_hex(3)
    _occ_or_fail(occ, ["group:add", "--display-name", name, rank_id])
    ranks_of(state).append({"id": rank_id, "nom": name})  # tout en haut ; on le déplace ensuite si besoin
    return rank_id


def delete_rank(occ, state, rank_id):
    ids = [rank["id"] for rank in ranks_of(state)]
    index = ids.index(rank_id)
    below = ids[index - 1] if index > 0 else None
    # Les personnes de ce rang descendent d'un cran (ou n'ont plus de rang).
    for user_id, rank in list(state.setdefault("rang_de", {}).items()):
        if rank == rank_id:
            if below:
                state["rang_de"][user_id] = below
            else:
                del state["rang_de"][user_id]
    state["rangs"] = [rank for rank in ranks_of(state) if rank["id"] != rank_id]
    occ(["group:delete", rank_id])


# ---- ajouté au découpage de main.py
import os
import threading
from services import activity_log
from services import notifications
from host import commands
from security import guard


def refresh_usage_in_background(user_ids=None):
    if user_ids is not None or time.time() - USAGE_CACHE["t"] > 600:
        USAGE_CACHE["t"] = time.time()
        threading.Thread(target=lambda: refresh_usage(commands.occ, user_ids), daemon=True).start()


def watch_signups():
    """Nouvelle inscription en attente : alerte aux super admins (une fois par compte)."""
    code, output, _ = commands.occ(["user:list", "--disabled", "--limit=0", "--output=json"])
    if code != 0:
        return
    try:
        disabled_accounts = json.loads(output or "{}")
    except ValueError:
        return
    state = read()
    seen = set(state["en_attente_vus"])
    pending = set()
    for user_id, name in disabled_accounts.items():
        item, setting, _ = commands.occ(["user:setting", user_id, "registration", "send_welcome_mail_on_enable"])
        if item == 0 and setting.strip() == "yes":
            pending.add(user_id)
            if user_id not in seen:
                notifications.send(
                    "comptes",
                    "Inscription à valider",
                    f"{name} ({user_id}) demande un compte Nextcloud.",
                    "/#/nuage",
                    family="nuage",
                    recipients=guard.super_admin_logins(),
                )
    if pending != seen:
        state["en_attente_vus"] = sorted(pending)
        write(state)
    # Un disque ajouté à la main dans Nextcloud (ou avant cette version) serait vu par tous les comptes.
    try:
        for name in reserve_disks_for_admins(commands.occ):
            activity_log.record(
                "nuage", f"Disque {name} réservé aux admins dans Nextcloud (il était visible par tous les comptes)"
            )
    except CloudError:
        pass
