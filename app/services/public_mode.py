"""Option « Serveur ouvert au public » : ce qui change quand des inconnus (adhérents d'un fablab…) ont un compte.

Sommaire
- read() / write()                         : les réglages (fichier public.json).
- LINK_POLICY, apply_link_policy(occ, on)  : liens publics Nextcloud avec mot de passe et date limite.
- privacy_notice(settings)                 : les mentions d'information RGPD, prêtes à coller dans la charte.
- inactive_accounts(accounts, months, now) : comptes sans connexion depuis N mois (à supprimer après avis).
- readiness(...)                           : la liste « avant d'ouvrir » (double vérification, sauvegardes…).
- create_file_request(ocs, path, days, pw) : un lien de dépôt (on dépose, on ne voit rien du dossier).
- watch_inactive_accounts(occ)             : une alerte par mois s'il y a des comptes inactifs.

Rien ne s'ouvre tout seul sur Internet : cette option règle Jabba et Nextcloud pour un
usage public. Brancher le domaine reste une étape à part, faite par l'admin.
"""

import datetime
import json
import os
import time

from services import notifications

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
SETTINGS_FILE = os.path.join(DATA_DIR, "public.json")
DEFAULTS = {
    "actif": False,
    "organisme": "",
    "contact": "",
    "inactif_mois": 12,
    "expiration_jours": 30,
    "avant": {},  # réglages Nextcloud d'avant, remis si on referme
    "derniere_alerte_inactifs": 0,
}
# Réglages de partage de Nextcloud (application « core ») appliqués en mode public.
LINK_POLICY = {
    "shareapi_enforce_links_password": "yes",
    "shareapi_default_expire_date": "yes",
    "shareapi_enforce_expire_date": "yes",
}
EXPIRE_DAYS_KEY = "shareapi_expire_after_n_days"
FILE_DROP_PERMISSION = 4  # « créer » seulement : déposer sans voir ni télécharger
PUBLIC_LINK = 3


def read():
    try:
        with open(SETTINGS_FILE) as file:
            return {**DEFAULTS, **json.load(file)}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def write(settings):
    os.makedirs(DATA_DIR, exist_ok=True)
    temporary_file = SETTINGS_FILE + ".tmp"
    with open(temporary_file, "w") as file:
        json.dump(settings, file)
    os.replace(temporary_file, SETTINGS_FILE)


# ---------------------------------------------------------------- liens publics de Nextcloud
def apply_link_policy(occ, enabled, settings):
    """Ouvert : mot de passe et date limite imposés. Refermé : on remet les réglages d'avant."""
    if enabled:
        before = {}
        for key in [*LINK_POLICY, EXPIRE_DAYS_KEY]:
            code, output, _ = occ(["config:app:get", "core", key])
            before[key] = output.strip() if code == 0 else None
        if not settings.get("avant"):
            settings["avant"] = before
        for key, value in LINK_POLICY.items():
            occ(["config:app:set", "core", key, "--value", value])
        occ(["config:app:set", "core", EXPIRE_DAYS_KEY, "--value", str(int(settings["expiration_jours"]))])
        return settings
    for key, value in (settings.get("avant") or {}).items():
        if value is None:
            occ(["config:app:delete", "core", key])
        else:
            occ(["config:app:set", "core", key, "--value", value])
    settings["avant"] = {}
    return settings


# ---------------------------------------------------------------- RGPD
def privacy_notice(settings):
    """Mentions d'information (RGPD, article 13), courtes, à placer dans la charte d'inscription."""
    organisation = settings.get("organisme") or "l'association qui gère ce serveur"
    contact = settings.get("contact") or "l'administrateur"
    months = int(settings.get("inactif_mois") or 12)
    return (
        f"Données personnelles : {organisation} conserve ton nom, ton adresse e-mail et les fichiers que tu déposes, "
        "uniquement pour te fournir cet espace de stockage. Rien n'est vendu ni transmis. "
        f"Ton compte est gardé tant que tu es membre ; après {months} mois sans connexion, il est supprimé après un avis. "
        "Tu peux télécharger tous tes fichiers à tout moment depuis Nextcloud. "
        f"Pour accéder à tes données, les corriger ou les effacer : {contact}. "
        "En cas de désaccord, tu peux saisir la CNIL (cnil.fr)."
    )


def _last_seen(account):
    value = account.get("vu")
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def inactive_accounts(accounts, months, now=None):
    """Comptes actifs (pas les admins) sans connexion depuis `months` mois, ou jamais connectés depuis leur création."""
    now = time.time() if now is None else now
    limit = now - months * 30.4 * 86400
    found = []
    for account in accounts:
        if account.get("admin") or account.get("etat") != "actif":
            continue
        seen = _last_seen(account)
        if seen is None:
            created = _last_seen({"vu": account.get("cree")})
            if created is not None and created < limit:
                found.append({**account, "inactif_depuis": None})
        elif seen < limit:
            found.append({**account, "inactif_depuis": int((now - seen) // 86400)})
    return found


# ---------------------------------------------------------------- avant d'ouvrir
def readiness(own_second_factor, backups_active, settings, nextcloud_ready):
    """Ce qu'il faut avant d'ouvrir à des inconnus : chaque point avec son état."""
    return [
        {
            "id": "double",
            "ok": own_second_factor,
            "texte": "Double vérification activée sur ton compte admin (Système).",
        },
        {"id": "sauvegardes", "ok": backups_active, "texte": "Sauvegardes en route (Sauvegardes)."},
        {
            "id": "nextcloud",
            "ok": nextcloud_ready,
            "texte": "Comptes Nextcloud mis en route, avec validation à la main.",
        },
        {
            "id": "mentions",
            "ok": bool(settings.get("organisme") and settings.get("contact")),
            "texte": "Nom de l'organisme et contact renseignés (mentions RGPD).",
        },
    ]


# ---------------------------------------------------------------- demande de fichiers
def create_file_request(ocs, path, days, password, label):
    """Un lien public « déposer seulement » sur un dossier Nextcloud. Renvoie {id, url, expire}."""
    days = max(1, min(int(days or 7), 90))
    expire = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
    data = {
        "path": path,
        "shareType": PUBLIC_LINK,
        "permissions": FILE_DROP_PERMISSION,
        "publicUpload": "true",
        "expireDate": expire,
        "label": (label or "Demande de fichiers")[:60],
    }
    if password:
        data["password"] = password
    share = ocs("POST", "", data)
    return {"id": str(share.get("id")), "url": share.get("url"), "expire": expire}


def watch_inactive_accounts(occ, list_accounts, now=None):
    now = time.time() if now is None else now
    settings = read()
    if not settings["actif"] or now - settings.get("derniere_alerte_inactifs", 0) < 30 * 86400:
        return
    inactive = inactive_accounts(list_accounts(occ), int(settings["inactif_mois"]), now)
    settings["derniere_alerte_inactifs"] = now
    write(settings)
    if inactive:
        notifications.send(
            "comptes",
            f"{len(inactive)} compte{'s' if len(inactive) > 1 else ''} inactif{'s' if len(inactive) > 1 else ''}",
            f"Sans connexion depuis plus de {settings['inactif_mois']} mois. Préviens-les, puis supprime-les depuis Comptes.",
            "/#/nuage",
            family="comptes",
        )
