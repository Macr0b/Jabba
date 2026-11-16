"""Comptes Nextcloud, gérés depuis Jabba (façon Dropbox).

- Inscription libre (appli Nextcloud « Registration ») : le compte est créé
  désactivé et attend la validation d'un super admin dans Jabba.
- Chaque compte a un espace (quota) : 5 Go par défaut. Les admins le changent,
  sans dépasser le plafond fixé par le super admin. Tout est noté au Journal.
- Dossiers communs (appli Nextcloud « Team folders », ex-« Group folders ») :
  visibles par tous les membres ou par un groupe, en lecture seule, en dépôt
  (ajouter et modifier, sans supprimer) ou en accès complet.

Tout passe par `occ`, la commande d'administration de Nextcloud. Ce module ne
connaît que la fonction `occ(args, entree=None, timeout=60) -> (code, sortie, erreur)`.
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
MODES = {   # droits Nextcloud : lire 1, modifier 2, créer 4, supprimer 8, partager 16
    "lecture": (["read"], "Lecture seule", "Voir et télécharger."),
    "depot": (["read", "write"], "Dépôt", "Voir, ajouter et modifier, sans rien supprimer."),
    "complet": (["read", "write", "share", "delete"], "Accès complet", "Tout faire, y compris supprimer et partager."),
}
MODES_BY_PERMISSIONS = {1: "lecture", 7: "depot", 31: "complet", 15: "complet", 23: "depot"}
GIGABYTE = 1024 ** 3
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
USAGE_CACHE = {"t": 0, "v": {}}   # identifiant -> octets utilisés (lu à part : une commande par compte)


class CloudError(Exception):
    pass


# ---------------------------------------------------------------- réglages de Jabba
def read():
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("plafond_go", 50)
    d.setdefault("en_attente_vus", [])    # inscriptions déjà signalées
    return d


def write(d):
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------- tailles
def to_bytes(q):
    """« 5 GB », « 500 MB », « none » (illimité) → octets, ou None si illimité."""
    q = str(q or "").strip()
    if q in ("", "none", "default"):
        return None
    m = re.fullmatch(r"([\d.]+)\s*([KMGT]?)i?B?", q, re.I)
    if not m:
        return None
    return int(float(m.group(1)) * 1024 ** "_KMGT".index((m.group(2) or "_").upper()))


def quota_text(go):
    """Nombre de Go → texte pour Nextcloud (« 5 GB »), ou « none » si illimité."""
    if go is None:
        return "none"
    go = float(go)
    return f"{int(go)} GB" if go == int(go) else f"{int(go * 1024)} MB"


# ---------------------------------------------------------------- lecture
def _occ_json(occ, args, timeout=60):
    code, sortie, err = occ(args + ["--output=json"], timeout=timeout)
    if code != 0:
        raise CloudError((err or sortie).strip()[-300:] or "Nextcloud ne répond pas.")
    try:
        return json.loads(sortie or "null")
    except ValueError:
        raise CloudError("Réponse de Nextcloud illisible.")


def overview(occ):
    """Nextcloud joignable ? Applis installées ? Réglages en place ?"""
    code, sortie, _ = occ(["status", "--output=json"], timeout=30)
    if code != 0:
        return {"present": False}
    try:
        st = json.loads(sortie)
    except ValueError:
        return {"present": False}
    applis = _occ_json(occ, ["app:list"]).get("enabled", {})
    reglages = {k: occ(["config:app:get", "registration", k])[1].strip()
                for k in ("admin_approval_required", "registered_user_group")} if "registration" in applis else {}
    defaut = occ(["config:app:get", "files", "default_quota"])[1].strip() or "none"
    return {
        "present": True, "version": st.get("versionstring"),
        "applis": {a: a in applis for a in APPS},
        "pret": all(a in applis for a in APPS) and reglages.get("admin_approval_required") == "yes",
        "quota_defaut": defaut,
    }


def accounts(occ):
    """Tous les comptes, avec leur état (actif, en attente, suspendu), groupes et espace."""
    brut = _occ_json(occ, ["user:list", "--info", "--limit=0"])
    res = []
    for uid, u in (brut or {}).items():
        attente = False
        if not u.get("enabled"):
            code, sortie, _ = occ(["user:setting", uid, "registration", "send_welcome_mail_on_enable"])
            attente = code == 0 and sortie.strip() == "yes"
        quota = to_bytes(u.get("quota"))
        res.append({
            "id": uid, "nom": u.get("display_name") or uid, "email": u.get("email") or "",
            "etat": "actif" if u.get("enabled") else ("attente" if attente else "suspendu"),
            "groupes": u.get("groups") or [], "admin": "admin" in (u.get("groups") or []),
            "quota": quota, "illimite": quota is None, "quota_texte": u.get("quota"),
            "utilise": USAGE_CACHE["v"].get(uid),
            "vu": None if u.get("last_seen") in (None, "never") else u.get("last_seen"),
            "cree": None if u.get("first_seen") in (None, "never") else u.get("first_seen"),
        })
    res.sort(key=lambda c: ({"attente": 0, "actif": 1, "suspendu": 2}[c["etat"]], c["nom"].lower()))
    return res


def refresh_usage(occ, uids=None):
    """L'espace utilisé par chaque compte (une commande par compte : fait à part, en fond)."""
    if uids is None:
        uids = list((_occ_json(occ, ["user:list", "--limit=0"]) or {}).keys())
    for uid in uids:
        code, sortie, _ = occ(["user:info", uid, "--output=json"])
        if code == 0:
            try:
                USAGE_CACHE["v"][uid] = ((json.loads(sortie).get("storage") or {}).get("used")) or 0
            except ValueError:
                pass
    USAGE_CACHE["t"] = time.time()


def groups(occ):
    brut = _occ_json(occ, ["group:list", "--limit=0"]) or {}
    return [{"id": g, "membres": m} for g, m in sorted(brut.items()) if g != "admin"]


def team_folders(occ):
    brut = _occ_json(occ, ["groupfolders:list"]) or []
    if isinstance(brut, dict):
        brut = list(brut.values())
    res = []
    for f in brut:
        acces = f.get("groups_list") or {g: v.get("permissions") if isinstance(v, dict) else v
                                         for g, v in (f.get("groups") or {}).items()}
        res.append({
            "id": f["id"], "nom": f.get("mountPoint") or f.get("mount_point"),
            "quota": None if (f.get("quota") or -3) < 0 else f["quota"], "taille": f.get("size") or 0,
            "acces": [{"groupe": g, "mode": MODES_BY_PERMISSIONS.get(int(p), "complet" if int(p) & 8 else "depot" if int(p) & 4 else "lecture")}
                      for g, p in sorted(acces.items())],
        })
    res.sort(key=lambda f: f["nom"].lower())
    return res


# ---------------------------------------------------------------- actions
def _occ_or_fail(occ, args, entree=None, timeout=60):
    code, sortie, err = occ(args, entree=entree, timeout=timeout)
    if code != 0:
        raise CloudError((err or sortie).strip()[-300:] or "Nextcloud a refusé.")
    return sortie


def set_up(occ, quota_defaut_go=5):
    """Installe les deux applis Nextcloud et pose les réglages : inscription validée par un admin, membres, 5 Go."""
    for a in APPS:
        code, _, _ = occ(["app:enable", a], timeout=120)
        if code != 0:
            _occ_or_fail(occ, ["app:install", a], timeout=600)
    code, _, _ = occ(["group:info", MEMBERS_GROUP])
    if code != 0:
        _occ_or_fail(occ, ["group:add", "--display-name", "Tous les membres", MEMBERS_GROUP])
    reglages = {
        "admin_approval_required": "yes",          # le compte attend la validation
        "registered_user_group": MEMBERS_GROUP,      # et rejoint « Tous les membres »
        "show_fullname": "yes", "enforce_fullname": "yes",
        # Pas encore de serveur d'e-mails : on ne vérifie pas l'adresse (à activer avec le domaine).
        "disable_email_verification": "yes",
    }
    for k, v in reglages.items():
        _occ_or_fail(occ, ["config:app:set", "registration", k, "--value", v])
    _occ_or_fail(occ, ["config:app:set", "files", "default_quota", "--value", quota_text(quota_defaut_go)])
    if not charter(occ):
        set_charter(occ, DEFAULT_CHARTER)
    reserve_disks_for_admins(occ)


def charter(occ):
    code, sortie, _ = occ(["config:app:get", "registration", "additional_hint"])
    return sortie.strip() if code == 0 else ""


def set_charter(occ, texte):
    texte = " ".join(str(texte or "").split())       # une seule ligne : le formulaire n'affiche pas les retours
    if len(texte) > CHARTER_MAX:
        raise CloudError(f"Charte trop longue ({CHARTER_MAX} caractères au plus).")
    if texte:
        _occ_or_fail(occ, ["config:app:set", "registration", "additional_hint", "--value", texte])
    else:
        occ(["config:app:delete", "registration", "additional_hint"])


def set_default_quota(occ, go):
    _occ_or_fail(occ, ["config:app:set", "files", "default_quota", "--value", quota_text(go)])


def reserve_disks_for_admins(occ):
    """Les disques branchés au serveur (stockages externes de Nextcloud) sont réservés aux admins.

    Un stockage externe sans personne de désignée est vu par TOUS les comptes : avant qu'un
    nouveau compte puisse entrer, chaque disque encore ouvert à tous est réservé au groupe admin.
    Un disque déjà réservé à des personnes ou des groupes précis n'est pas touché."""
    code, sortie, _ = occ(["files_external:list", "--output=json"])
    if code != 0:
        return []          # appli des stockages externes absente : aucun disque dans Nextcloud
    try:
        montages = json.loads(sortie)
    except ValueError:
        raise CloudError("Nextcloud : liste des disques illisible.")
    faits = []
    for m in montages if isinstance(montages, list) else []:
        if not m.get("applicable_users") and not m.get("applicable_groups"):
            _occ_or_fail(occ, ["files_external:applicable", "--add-group=admin", str(m["mount_id"])])
            faits.append(str(m.get("mount_point", "")).strip("/"))
    return faits


def approve(occ, uid):
    reserve_disks_for_admins(occ)
    _occ_or_fail(occ, ["user:enable", uid])
    occ(["group:adduser", MEMBERS_GROUP, uid])


def suspend(occ, uid, actif):
    _occ_or_fail(occ, ["user:enable" if actif else "user:disable", uid])


def delete(occ, uid):
    _occ_or_fail(occ, ["user:delete", uid], timeout=600)
    USAGE_CACHE["v"].pop(uid, None)


def set_quota(occ, uid, go):
    _occ_or_fail(occ, ["user:setting", uid, "files", "quota", quota_text(go)])


def add(occ, uid, nom, email, mdp, go=None):
    """Compte créé par un admin (pas d'inscription) : actif tout de suite, membre de « Tous »."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,31}", uid):
        raise CloudError("Identifiant : 2 à 32 caractères, lettres minuscules, chiffres, point, tiret.")
    reserve_disks_for_admins(occ)
    args = ["user:add", "--password-from-env", "--group", MEMBERS_GROUP]
    if nom:
        args += ["--display-name", nom]
    if email:
        args += ["--email", email]
    _occ_with_password(occ, args + [uid], mdp)
    if go is not None:
        set_quota(occ, uid, go)


def set_password(occ, uid, mdp):
    _occ_with_password(occ, ["user:resetpassword", "--password-from-env", uid], mdp)


def _occ_with_password(occ, args, mdp):
    # Le mot de passe passe par l'entrée standard, jamais dans la ligne de commande.
    code, sortie, err = occ(args, entree=mdp, mdp=True, timeout=60)
    if code != 0:
        raise CloudError((err or sortie).strip()[-300:] or "Nextcloud a refusé.")


def set_group_member(occ, groupe, uid, dedans):
    _occ_or_fail(occ, ["group:adduser" if dedans else "group:removeuser", groupe, uid])


def create_group(occ, nom):
    if not re.fullmatch(r"[\w .-]{2,40}", nom) or nom.lower() == "admin" or nom.lower().startswith(RANK_PREFIX):
        raise CloudError("Nom de groupe : 2 à 40 lettres, chiffres, espaces ou tirets.")
    _occ_or_fail(occ, ["group:add", nom])


def delete_group(occ, nom):
    if nom in ("admin", MEMBERS_GROUP) or nom.startswith(RANK_PREFIX):
        raise CloudError("Ce groupe ne se supprime pas.")
    _occ_or_fail(occ, ["group:delete", nom])


def create_team_folder(occ, nom):
    nom = nom.strip()
    if not nom or "/" in nom or len(nom) > 60:
        raise CloudError("Nom de dossier invalide.")
    return int(_occ_or_fail(occ, ["groupfolders:create", nom]).strip().splitlines()[-1])


def set_team_folder_access(occ, fid, groupe, mode):
    """mode : lecture, depot, complet ; ou None pour retirer l'accès au groupe."""
    if mode is None:
        _occ_or_fail(occ, ["groupfolders:group", "--delete", str(fid), groupe])
    else:
        _occ_or_fail(occ, ["groupfolders:group", str(fid), groupe] + MODES[mode][0])


def set_team_folder_quota(occ, fid, go):
    _occ_or_fail(occ, ["groupfolders:quota", str(fid), "unlimited" if go is None else quota_text(go)])


def rename_team_folder(occ, fid, nom):
    if not nom.strip() or "/" in nom:
        raise CloudError("Nom de dossier invalide.")
    _occ_or_fail(occ, ["groupfolders:rename", str(fid), nom.strip()])


def delete_team_folder(occ, fid):
    _occ_or_fail(occ, ["groupfolders:delete", "--force", str(fid)], timeout=600)


# ---------------------------------------------------------------- rangs
# Option : des rangs rangés du plus bas au plus haut (Adhérent < Employé < Gérant…),
# nommés librement par le compte du serveur. Chaque rang est un groupe Nextcloud ;
# une personne fait partie du groupe de son rang ET de ceux en dessous. Un dossier
# commun donné à un rang est donc vu par ce rang et ceux au-dessus, pas en dessous ;
# et si plusieurs rangs y ont accès, chacun garde le meilleur accès qui le concerne.
RANK_PREFIX = "rang-"


def ranks_of(d):
    """Les rangs, du plus bas au plus haut : [{id, nom}]."""
    return d.setdefault("rangs", [])


def rank_name(d, rid):
    return next((r["nom"] for r in ranks_of(d) if r["id"] == rid), None)


def wanted_groups(d, uid):
    """Groupes de rang dont `uid` doit faire partie (son rang et ceux en dessous)."""
    if not d.get("rangs_actif"):
        return set()
    rid = d.setdefault("rang_de", {}).get(uid)
    ids = [r["id"] for r in ranks_of(d)]
    if rid not in ids:
        return set()
    return set(ids[: ids.index(rid) + 1])


def sync_ranks(occ, d, uids=None):
    """Met les groupes Nextcloud en accord avec les rangs (après un changement de rang, d'ordre, ou l'option)."""
    membres = _occ_json(occ, ["group:list", "--limit=0"]) or {}
    tous_rangs = {g for g in membres if g.startswith(RANK_PREFIX)}
    if uids is None:
        uids = set(d.get("rang_de", {})) | {u for g in tous_rangs for u in membres.get(g, [])}
    for uid in uids:
        voulus = wanted_groups(d, uid)
        for g in tous_rangs | voulus:
            dedans = uid in membres.get(g, [])
            if g in voulus and not dedans:
                occ(["group:adduser", g, uid])
            elif g not in voulus and dedans:
                occ(["group:removeuser", g, uid])


def create_rank(occ, d, nom):
    import secrets
    nom = nom.strip()
    if not nom or len(nom) > 40:
        raise CloudError("Nom de rang : 1 à 40 caractères.")
    if any(r["nom"].lower() == nom.lower() for r in ranks_of(d)):
        raise CloudError("Ce rang existe déjà.")
    rid = RANK_PREFIX + secrets.token_hex(3)
    _occ_or_fail(occ, ["group:add", "--display-name", nom, rid])
    ranks_of(d).append({"id": rid, "nom": nom})   # tout en haut ; on le déplace ensuite si besoin
    return rid


def delete_rank(occ, d, rid):
    ids = [r["id"] for r in ranks_of(d)]
    i = ids.index(rid)
    dessous = ids[i - 1] if i > 0 else None
    # Les personnes de ce rang descendent d'un cran (ou n'ont plus de rang).
    for uid, r in list(d.setdefault("rang_de", {}).items()):
        if r == rid:
            if dessous:
                d["rang_de"][uid] = dessous
            else:
                del d["rang_de"][uid]
    d["rangs"] = [r for r in ranks_of(d) if r["id"] != rid]
    occ(["group:delete", rid])


# ---- ajouté au découpage de main.py
import os
import threading
from services import activity_log
from services import notifications
from host import commands
from security import guard


def refresh_usage_in_background(uids=None):
    if uids is not None or time.time() - USAGE_CACHE["t"] > 600:
        USAGE_CACHE["t"] = time.time()
        threading.Thread(target=lambda: refresh_usage(commands.occ, uids), daemon=True).start()


def watch_signups():
    """Nouvelle inscription en attente : alerte aux super admins (une fois par compte)."""
    code, sortie, _ = commands.occ(["user:list", "--disabled", "--limit=0", "--output=json"])
    if code != 0:
        return
    try:
        desactives = json.loads(sortie or "{}")
    except ValueError:
        return
    d = read()
    vus = set(d["en_attente_vus"])
    attente = set()
    for uid, nom in desactives.items():
        c, s, _ = commands.occ(["user:setting", uid, "registration", "send_welcome_mail_on_enable"])
        if c == 0 and s.strip() == "yes":
            attente.add(uid)
            if uid not in vus:
                notifications.send("comptes", "Inscription à valider", f"{nom} ({uid}) demande un compte Nextcloud.", "/#/nuage",
                               famille="nuage", pour=guard.super_admin_logins())
    if attente != vus:
        d["en_attente_vus"] = sorted(attente)
        write(d)
    # Un disque ajouté à la main dans Nextcloud (ou avant cette version) serait vu par tous les comptes.
    try:
        for nom in reserve_disks_for_admins(commands.occ):
            activity_log.record("nuage", f"Disque {nom} réservé aux admins dans Nextcloud (il était visible par tous les comptes)")
    except CloudError:
        pass
