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

DOSSIER = os.environ.get("JABBA_DATA", "/data")
FICHIER = os.path.join(DOSSIER, "nuage.json")
GROUPE_TOUS = "membres"
APPLIS = ("registration", "groupfolders")
MODES = {   # droits Nextcloud : lire 1, modifier 2, créer 4, supprimer 8, partager 16
    "lecture": (["read"], "Lecture seule", "Voir et télécharger."),
    "depot": (["read", "write"], "Dépôt", "Voir, ajouter et modifier, sans rien supprimer."),
    "complet": (["read", "write", "share", "delete"], "Accès complet", "Tout faire, y compris supprimer et partager."),
}
NOMBRES_MODES = {1: "lecture", 7: "depot", 31: "complet", 15: "complet", 23: "depot"}
GO = 1024 ** 3
# Charte affichée sur le formulaire d'inscription (modifiable dans Jabba).
CHARTE_MAX = 1500
CHARTE_DEFAUT = (
    "Ce service est hébergé par un particulier, sur son propre serveur, et chaque compte est validé à la main. "
    "L'administrateur peut techniquement accéder aux fichiers stockés ici : il s'engage à ne pas le faire, "
    "sauf à ta demande ou par obligation légale. Pour des fichiers vraiment privés, chiffre-les toi-même avec "
    "Cryptomator (cryptomator.org) : personne d'autre ne pourra les lire, l'administrateur non plus. "
    "Tout est sauvegardé chaque nuit, mais garde ta propre copie de ce qui compte. "
    "Tu peux demander à tout moment la suppression de ton compte et de tes fichiers. "
    "En créant ton compte, tu acceptes cette charte."
)
_verrou = threading.RLock()
USAGE = {"t": 0, "v": {}}   # identifiant -> octets utilisés (lu à part : une commande par compte)


class Erreur(Exception):
    pass


# ---------------------------------------------------------------- réglages de Jabba
def lire():
    try:
        with open(FICHIER) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("plafond_go", 50)
    d.setdefault("en_attente_vus", [])    # inscriptions déjà signalées
    return d


def ecrire(d):
    with _verrou:
        os.makedirs(DOSSIER, exist_ok=True)
        tmp = FICHIER + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, FICHIER)


# ---------------------------------------------------------------- tailles
def en_octets(q):
    """« 5 GB », « 500 MB », « none » (illimité) → octets, ou None si illimité."""
    q = str(q or "").strip()
    if q in ("", "none", "default"):
        return None
    m = re.fullmatch(r"([\d.]+)\s*([KMGT]?)i?B?", q, re.I)
    if not m:
        return None
    return int(float(m.group(1)) * 1024 ** "_KMGT".index((m.group(2) or "_").upper()))


def texte_quota(go):
    """Nombre de Go → texte pour Nextcloud (« 5 GB »), ou « none » si illimité."""
    if go is None:
        return "none"
    go = float(go)
    return f"{int(go)} GB" if go == int(go) else f"{int(go * 1024)} MB"


# ---------------------------------------------------------------- lecture
def _json(occ, args, timeout=60):
    code, sortie, err = occ(args + ["--output=json"], timeout=timeout)
    if code != 0:
        raise Erreur((err or sortie).strip()[-300:] or "Nextcloud ne répond pas.")
    try:
        return json.loads(sortie or "null")
    except ValueError:
        raise Erreur("Réponse de Nextcloud illisible.")


def etat(occ):
    """Nextcloud joignable ? Applis installées ? Réglages en place ?"""
    code, sortie, _ = occ(["status", "--output=json"], timeout=30)
    if code != 0:
        return {"present": False}
    try:
        st = json.loads(sortie)
    except ValueError:
        return {"present": False}
    applis = _json(occ, ["app:list"]).get("enabled", {})
    reglages = {k: occ(["config:app:get", "registration", k])[1].strip()
                for k in ("admin_approval_required", "registered_user_group")} if "registration" in applis else {}
    defaut = occ(["config:app:get", "files", "default_quota"])[1].strip() or "none"
    return {
        "present": True, "version": st.get("versionstring"),
        "applis": {a: a in applis for a in APPLIS},
        "pret": all(a in applis for a in APPLIS) and reglages.get("admin_approval_required") == "yes",
        "quota_defaut": defaut,
    }


def comptes(occ):
    """Tous les comptes, avec leur état (actif, en attente, suspendu), groupes et espace."""
    brut = _json(occ, ["user:list", "--info", "--limit=0"])
    res = []
    for uid, u in (brut or {}).items():
        attente = False
        if not u.get("enabled"):
            code, sortie, _ = occ(["user:setting", uid, "registration", "send_welcome_mail_on_enable"])
            attente = code == 0 and sortie.strip() == "yes"
        quota = en_octets(u.get("quota"))
        res.append({
            "id": uid, "nom": u.get("display_name") or uid, "email": u.get("email") or "",
            "etat": "actif" if u.get("enabled") else ("attente" if attente else "suspendu"),
            "groupes": u.get("groups") or [], "admin": "admin" in (u.get("groups") or []),
            "quota": quota, "illimite": quota is None, "quota_texte": u.get("quota"),
            "utilise": USAGE["v"].get(uid),
            "vu": None if u.get("last_seen") in (None, "never") else u.get("last_seen"),
            "cree": None if u.get("first_seen") in (None, "never") else u.get("first_seen"),
        })
    res.sort(key=lambda c: ({"attente": 0, "actif": 1, "suspendu": 2}[c["etat"]], c["nom"].lower()))
    return res


def actualiser_usage(occ, uids=None):
    """L'espace utilisé par chaque compte (une commande par compte : fait à part, en fond)."""
    if uids is None:
        uids = list((_json(occ, ["user:list", "--limit=0"]) or {}).keys())
    for uid in uids:
        code, sortie, _ = occ(["user:info", uid, "--output=json"])
        if code == 0:
            try:
                USAGE["v"][uid] = ((json.loads(sortie).get("storage") or {}).get("used")) or 0
            except ValueError:
                pass
    USAGE["t"] = time.time()


def groupes(occ):
    brut = _json(occ, ["group:list", "--limit=0"]) or {}
    return [{"id": g, "membres": m} for g, m in sorted(brut.items()) if g != "admin"]


def dossiers(occ):
    brut = _json(occ, ["groupfolders:list"]) or []
    if isinstance(brut, dict):
        brut = list(brut.values())
    res = []
    for f in brut:
        acces = f.get("groups_list") or {g: v.get("permissions") if isinstance(v, dict) else v
                                         for g, v in (f.get("groups") or {}).items()}
        res.append({
            "id": f["id"], "nom": f.get("mountPoint") or f.get("mount_point"),
            "quota": None if (f.get("quota") or -3) < 0 else f["quota"], "taille": f.get("size") or 0,
            "acces": [{"groupe": g, "mode": NOMBRES_MODES.get(int(p), "complet" if int(p) & 8 else "depot" if int(p) & 4 else "lecture")}
                      for g, p in sorted(acces.items())],
        })
    res.sort(key=lambda f: f["nom"].lower())
    return res


# ---------------------------------------------------------------- actions
def _faire(occ, args, entree=None, timeout=60):
    code, sortie, err = occ(args, entree=entree, timeout=timeout)
    if code != 0:
        raise Erreur((err or sortie).strip()[-300:] or "Nextcloud a refusé.")
    return sortie


def mettre_en_route(occ, quota_defaut_go=5):
    """Installe les deux applis Nextcloud et pose les réglages : inscription validée par un admin, membres, 5 Go."""
    for a in APPLIS:
        code, _, _ = occ(["app:enable", a], timeout=120)
        if code != 0:
            _faire(occ, ["app:install", a], timeout=600)
    code, _, _ = occ(["group:info", GROUPE_TOUS])
    if code != 0:
        _faire(occ, ["group:add", "--display-name", "Tous les membres", GROUPE_TOUS])
    reglages = {
        "admin_approval_required": "yes",          # le compte attend la validation
        "registered_user_group": GROUPE_TOUS,      # et rejoint « Tous les membres »
        "show_fullname": "yes", "enforce_fullname": "yes",
        # Pas encore de serveur d'e-mails : on ne vérifie pas l'adresse (à activer avec le domaine).
        "disable_email_verification": "yes",
    }
    for k, v in reglages.items():
        _faire(occ, ["config:app:set", "registration", k, "--value", v])
    _faire(occ, ["config:app:set", "files", "default_quota", "--value", texte_quota(quota_defaut_go)])
    if not charte(occ):
        poser_charte(occ, CHARTE_DEFAUT)


def charte(occ):
    code, sortie, _ = occ(["config:app:get", "registration", "additional_hint"])
    return sortie.strip() if code == 0 else ""


def poser_charte(occ, texte):
    texte = " ".join(str(texte or "").split())       # une seule ligne : le formulaire n'affiche pas les retours
    if len(texte) > CHARTE_MAX:
        raise Erreur(f"Charte trop longue ({CHARTE_MAX} caractères au plus).")
    if texte:
        _faire(occ, ["config:app:set", "registration", "additional_hint", "--value", texte])
    else:
        occ(["config:app:delete", "registration", "additional_hint"])


def quota_defaut(occ, go):
    _faire(occ, ["config:app:set", "files", "default_quota", "--value", texte_quota(go)])


def valider(occ, uid):
    _faire(occ, ["user:enable", uid])
    occ(["group:adduser", GROUPE_TOUS, uid])


def suspendre(occ, uid, actif):
    _faire(occ, ["user:enable" if actif else "user:disable", uid])


def supprimer(occ, uid):
    _faire(occ, ["user:delete", uid], timeout=600)
    USAGE["v"].pop(uid, None)


def changer_quota(occ, uid, go):
    _faire(occ, ["user:setting", uid, "files", "quota", texte_quota(go)])


def ajouter(occ, uid, nom, email, mdp, go=None):
    """Compte créé par un admin (pas d'inscription) : actif tout de suite, membre de « Tous »."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,31}", uid):
        raise Erreur("Identifiant : 2 à 32 caractères, lettres minuscules, chiffres, point, tiret.")
    args = ["user:add", "--password-from-env", "--group", GROUPE_TOUS]
    if nom:
        args += ["--display-name", nom]
    if email:
        args += ["--email", email]
    _faire_mdp(occ, args + [uid], mdp)
    if go is not None:
        changer_quota(occ, uid, go)


def nouveau_mdp(occ, uid, mdp):
    _faire_mdp(occ, ["user:resetpassword", "--password-from-env", uid], mdp)


def _faire_mdp(occ, args, mdp):
    # Le mot de passe passe par l'entrée standard, jamais dans la ligne de commande.
    code, sortie, err = occ(args, entree=mdp, mdp=True, timeout=60)
    if code != 0:
        raise Erreur((err or sortie).strip()[-300:] or "Nextcloud a refusé.")


def groupe_membres(occ, groupe, uid, dedans):
    _faire(occ, ["group:adduser" if dedans else "group:removeuser", groupe, uid])


def nouveau_groupe(occ, nom):
    if not re.fullmatch(r"[\w .-]{2,40}", nom) or nom.lower() == "admin" or nom.lower().startswith(PREFIXE_RANG):
        raise Erreur("Nom de groupe : 2 à 40 lettres, chiffres, espaces ou tirets.")
    _faire(occ, ["group:add", nom])


def supprimer_groupe(occ, nom):
    if nom in ("admin", GROUPE_TOUS) or nom.startswith(PREFIXE_RANG):
        raise Erreur("Ce groupe ne se supprime pas.")
    _faire(occ, ["group:delete", nom])


def nouveau_dossier(occ, nom):
    nom = nom.strip()
    if not nom or "/" in nom or len(nom) > 60:
        raise Erreur("Nom de dossier invalide.")
    return int(_faire(occ, ["groupfolders:create", nom]).strip().splitlines()[-1])


def dossier_acces(occ, fid, groupe, mode):
    """mode : lecture, depot, complet ; ou None pour retirer l'accès au groupe."""
    if mode is None:
        _faire(occ, ["groupfolders:group", "--delete", str(fid), groupe])
    else:
        _faire(occ, ["groupfolders:group", str(fid), groupe] + MODES[mode][0])


def dossier_quota(occ, fid, go):
    _faire(occ, ["groupfolders:quota", str(fid), "unlimited" if go is None else texte_quota(go)])


def renommer_dossier(occ, fid, nom):
    if not nom.strip() or "/" in nom:
        raise Erreur("Nom de dossier invalide.")
    _faire(occ, ["groupfolders:rename", str(fid), nom.strip()])


def supprimer_dossier(occ, fid):
    _faire(occ, ["groupfolders:delete", "--force", str(fid)], timeout=600)


# ---------------------------------------------------------------- rangs
# Option : des rangs rangés du plus bas au plus haut (Adhérent < Employé < Gérant…),
# nommés librement par le compte du serveur. Chaque rang est un groupe Nextcloud ;
# une personne fait partie du groupe de son rang ET de ceux en dessous. Un dossier
# commun donné à un rang est donc vu par ce rang et ceux au-dessus, pas en dessous ;
# et si plusieurs rangs y ont accès, chacun garde le meilleur accès qui le concerne.
PREFIXE_RANG = "rang-"


def rangs_de(d):
    """Les rangs, du plus bas au plus haut : [{id, nom}]."""
    return d.setdefault("rangs", [])


def nom_rang(d, rid):
    return next((r["nom"] for r in rangs_de(d) if r["id"] == rid), None)


def groupes_voulus(d, uid):
    """Groupes de rang dont `uid` doit faire partie (son rang et ceux en dessous)."""
    if not d.get("rangs_actif"):
        return set()
    rid = d.setdefault("rang_de", {}).get(uid)
    ids = [r["id"] for r in rangs_de(d)]
    if rid not in ids:
        return set()
    return set(ids[: ids.index(rid) + 1])


def synchroniser(occ, d, uids=None):
    """Met les groupes Nextcloud en accord avec les rangs (après un changement de rang, d'ordre, ou l'option)."""
    membres = _json(occ, ["group:list", "--limit=0"]) or {}
    tous_rangs = {g for g in membres if g.startswith(PREFIXE_RANG)}
    if uids is None:
        uids = set(d.get("rang_de", {})) | {u for g in tous_rangs for u in membres.get(g, [])}
    for uid in uids:
        voulus = groupes_voulus(d, uid)
        for g in tous_rangs | voulus:
            dedans = uid in membres.get(g, [])
            if g in voulus and not dedans:
                occ(["group:adduser", g, uid])
            elif g not in voulus and dedans:
                occ(["group:removeuser", g, uid])


def creer_rang(occ, d, nom):
    import secrets
    nom = nom.strip()
    if not nom or len(nom) > 40:
        raise Erreur("Nom de rang : 1 à 40 caractères.")
    if any(r["nom"].lower() == nom.lower() for r in rangs_de(d)):
        raise Erreur("Ce rang existe déjà.")
    rid = PREFIXE_RANG + secrets.token_hex(3)
    _faire(occ, ["group:add", "--display-name", nom, rid])
    rangs_de(d).append({"id": rid, "nom": nom})   # tout en haut ; on le déplace ensuite si besoin
    return rid


def supprimer_rang(occ, d, rid):
    ids = [r["id"] for r in rangs_de(d)]
    i = ids.index(rid)
    dessous = ids[i - 1] if i > 0 else None
    # Les personnes de ce rang descendent d'un cran (ou n'ont plus de rang).
    for uid, r in list(d.setdefault("rang_de", {}).items()):
        if r == rid:
            if dessous:
                d["rang_de"][uid] = dessous
            else:
                del d["rang_de"][uid]
    d["rangs"] = [r for r in rangs_de(d) if r["id"] != rid]
    occ(["group:delete", rid])
