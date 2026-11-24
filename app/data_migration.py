"""Données d'avant la 0.17 : les clés françaises des fichiers JSON passent en anglais.

Sommaire
- RENAMED_KEYS             : ancien nom de clé → nouveau nom.
- KEYED_BY_NAME            : fichiers dont les clés du premier niveau sont des noms (comptes, écrans…).
- NAMES_INSIDE             : clés dont le contenu est rangé par noms de choix (types d'alertes, sources).
- convert(value, top_names): la donnée avec ses clés renommées.
- convert_file(path, copy_dir): convertit un fichier (.json ou .jsonl), copie de l'original avant.
  interne : _read, _text
- run(data_dir)            : convertit tout le dossier des données ; rien à faire la deuxième fois.

Lancé avant l'appli (Dockerfile, puis uvicorn) : aucun module ne lit un fichier pas encore converti.
Une sauvegarde restaurée plus tard est convertie au redémarrage suivant, de la même façon.
Les noms de fichiers ne changent pas : ils se voient sur le serveur, comme les dossiers.
"""

import json
import os
import shutil
import sys

COPY_FOLDER = "copie-avant-0.17"
SKIPPED_FOLDERS = {COPY_FOLDER, "vignettes"}
KEYED_BY_NAME = {
    "rangement.json",
    "double-verification.json",
    "identifiants-outils.json",
    "adresses-outils.json",
    "sessions.json",
}
NAMES_INSIDE = {"types", "sources"}
RENAMED_KEYS = {
    "a_jour": "up_to_date",
    "abonne": "subscribed",
    "abonnement": "subscription",
    "abonnements": "subscriptions",
    "acces": "access",
    "actif": "active",
    "actuelle": "current",
    "adresse": "address",
    "ajoute": "added",
    "alerte_absent": "alert_missing",
    "alerte_echec": "alert_failure",
    "analyse": "scan",
    "analyses": "scans",
    "ancien": "old",
    "appareil": "device",
    "appareils": "devices",
    "appli": "app",
    "applis": "apps",
    "arret_prevu": "shutdown_planned",
    "attente": "pending",
    "autre": "other",
    "autre_personne": "other_person",
    "avant": "before",
    "avert": "warning",
    "batterie": "battery",
    "besoin": "needed",
    "branche": "plugged",
    "canaux": "channel_count",
    "carte": "card",
    "certificat_ok": "certificate_ok",
    "champ": "field",
    "champs": "fields",
    "changements": "changes",
    "chaque_semaine": "weekly",
    "charte": "charter",
    "charte_defaut": "default_charter",
    "charte_max": "charter_max",
    "chemin": "path",
    "chemin_dev": "dev_path",
    "chemins": "paths",
    "choisi": "chosen",
    "cible": "target",
    "cle": "key",
    "cle_jellyfin": "jellyfin_key",
    "cle_prete": "key_ready",
    "cle_publique": "public_key",
    "cles": "keys",
    "cloudflare_lien": "cloudflare_link",
    "code_existe": "code_exists",
    "codes_restants": "codes_left",
    "commande": "command",
    "complet": "full",
    "compte": "account",
    "comptes": "accounts",
    "compteur": "counter",
    "compteurs": "counters",
    "confirme": "confirmed",
    "connecte": "connected",
    "conso": "consumption",
    "conteneur": "container",
    "conteneurs": "containers",
    "contenu": "content",
    "copie": "copy",
    "cote": "side",
    "courant": "power",
    "createur": "creator",
    "cree": "created",
    "cree_par": "created_by",
    "cree_par_jabba": "created_by_jabba",
    "dans_appli": "in_app",
    "de": "source",
    "de_base": "basic",
    "debut": "start",
    "dedans": "inside",
    "defaut": "default",
    "defi": "challenge",
    "delai": "delay",
    "demandes": "requests",
    "demandes_n": "request_count",
    "demandeur": "requester",
    "depot": "repository",
    "depuis": "since",
    "dernier": "last",
    "dernier_creneau": "last_slot",
    "dernier_lien_nc": "last_nc_link",
    "derniere": "latest",
    "derniere_alerte": "last_alert",
    "derniere_alerte_inactifs": "last_inactive_alert",
    "derniere_analyse": "last_scan",
    "derniere_connexion": "last_login",
    "derniere_maj_auto": "last_auto_update",
    "derniere_purge": "last_purge",
    "derniere_reussie": "last_success",
    "directe": "direct",
    "disponible": "available",
    "disque": "disk",
    "disque_jabba": "jabba_disk",
    "disques": "disks",
    "disques_pleins": "full_disks",
    "domaine": "domain",
    "donnees": "data",
    "dossier": "folder",
    "dossiers": "folders",
    "du": "due",
    "duree": "duration",
    "ecart": "quarantined",
    "echeance": "due_date",
    "ecrire": "write",
    "ecrit": "written",
    "ecriture": "writable",
    "elements": "items",
    "emplacement": "location",
    "empreinte": "fingerprint",
    "empreintes": "fingerprints",
    "en_attente_vus": "pending_seen",
    "en_cours": "in_progress",
    "en_ligne": "online",
    "en_option": "optional",
    "endormi": "asleep",
    "entetes": "headers",
    "entre": "between",
    "entree": "entry",
    "entrees": "entries",
    "envoyes": "sent",
    "erreur": "error",
    "espace": "space",
    "espace_admins": "admin_space",
    "essai": "attempt",
    "essais": "attempts",
    "essentiels": "essentials",
    "etape": "step",
    "etat": "state",
    "etiquette": "label",
    "expiration_jours": "expiry_days",
    "externe": "external",
    "fait": "done",
    "famille": "family",
    "favori": "favorite",
    "favoris": "favorites",
    "fichier": "file",
    "fichiers": "files",
    "fin": "end",
    "fini": "finished",
    "garde": "kept",
    "geste": "gesture",
    "glisse": "dragging",
    "groupe": "group",
    "groupes": "groups",
    "hauteur": "height",
    "heure": "hour",
    "heures": "hours",
    "horodatage": "timestamp",
    "icone": "icon",
    "identifiant": "username",
    "identifiants": "credentials",
    "identifiants_jabba": "jabba_credentials",
    "ignores": "ignored",
    "illimite": "unlimited",
    "illisible": "unreadable",
    "inactif_depuis": "inactive_since",
    "inactif_mois": "inactive_months",
    "inactifs": "inactive",
    "incrustation": "picture_in_picture",
    "indice": "hint",
    "initiale": "initial",
    "injoignable": "unreachable",
    "installe": "installed",
    "instantane": "snapshot",
    "integre": "built_in",
    "invite": "guest",
    "ip_box": "router_ip",
    "ip_serveur": "server_ip",
    "jeton": "token",
    "journal": "log",
    "jours": "days",
    "lance": "started",
    "lancee": "launched",
    "largeur": "width",
    "libelle": "label_text",
    "libre": "free",
    "lie": "linked",
    "lien": "link",
    "lien_nextcloud": "nextcloud_link",
    "lien_tailscale": "tailscale_link",
    "lu": "read",
    "maj_signalee": "update_reported",
    "masque": "hidden",
    "masques": "hidden_items",
    "mdp": "password",
    "membres": "members",
    "memoire": "memory",
    "memoire_go": "memory_gb",
    "menace": "threat",
    "menaces": "threats",
    "menage": "cleanup",
    "methode": "method",
    "methodes": "methods",
    "minuteur": "timer",
    "modele": "model",
    "modifiable": "editable",
    "modifie": "modified",
    "modifies": "modified_items",
    "moi": "me",
    "mois": "months",
    "montage": "mount",
    "mot_de_passe": "password",
    "mot_de_passe_appli": "app_password",
    "muet": "muted",
    "niveau": "level",
    "nom": "name",
    "nom_local": "local_name",
    "noms_liberes": "freed_names",
    "nouveau": "new",
    "nouveaux": "new_items",
    "nouveaux_depuis": "new_since",
    "offre": "offer",
    "onglet": "tab",
    "ordre": "order",
    "organisme": "organisation",
    "origine": "origin",
    "oublie": "forgotten",
    "oublies": "forgotten_items",
    "outils": "tools",
    "outils_actifs": "active_tools",
    "outils_total": "tools_total",
    "ouvertures": "opens",
    "ovh_lien": "ovh_link",
    "ovh_pret": "ovh_ready",
    "page_cles": "keys_page",
    "pannes": "failures",
    "par": "by",
    "partages": "shares",
    "pieces": "parts",
    "pile": "stack",
    "pilote": "driver",
    "plafond_go": "cap_gb",
    "poignee": "handle",
    "pont_sat": "sat_bridge",
    "port_serveur": "server_port",
    "pourcent": "percent",
    "prefixe": "prefix",
    "premiere": "first",
    "preparer": "prepare",
    "pret": "ready",
    "processeur": "cpu",
    "prochaine": "next",
    "promis": "promised",
    "proprietaires": "owners",
    "protege": "protected",
    "provisoire": "temporary",
    "provisoire_exp": "temporary_exp",
    "qui": "who",
    "quota_defaut": "default_quota",
    "quota_defaut_go": "default_quota_gb",
    "quota_texte": "quota_text",
    "raisons": "reasons",
    "rang": "rank",
    "rang_de": "rank_of",
    "rang_nuage": "cloud_rank",
    "rangement": "arrangement",
    "rangs": "ranks",
    "rangs_actif": "ranks_enabled",
    "rappels": "reminders",
    "rappels_dus": "reminders_due",
    "redemarrage": "restart",
    "redemarrage_auto": "auto_restart",
    "redemarrage_signale": "restart_reported",
    "refus": "refusals",
    "reglages": "settings",
    "remis": "reset",
    "remis_a": "reset_to",
    "remise": "password_reset",
    "remplace": "replaces",
    "repos": "idle",
    "restaures": "restored",
    "resume": "summary",
    "retire": "removed",
    "saut": "skip",
    "secondaire": "secondary",
    "secteur": "mains",
    "sel": "salt",
    "semaines": "weeks",
    "sens": "direction",
    "serie": "streak",
    "sert_a": "purpose",
    "serveur": "server",
    "signale": "reported",
    "signales": "reported_items",
    "son": "sound",
    "sortie": "output",
    "soucis": "issues",
    "sous": "subtitle",
    "soustitres": "subtitles",
    "statut": "status",
    "supprime": "deleted",
    "systeme": "system",
    "tache": "task",
    "taille": "size",
    "technique": "technical",
    "teste": "tested",
    "texte": "text",
    "titre": "title",
    "tous": "all",
    "tous_les_jours": "every_days",
    "tout": "everything",
    "tri": "sort",
    "unite": "unit",
    "usure": "wear",
    "utilisateur": "user",
    "utilise": "used",
    "utilisee": "used_space",
    "valeur": "value",
    "variable_adresse": "address_variable",
    "vers": "destination",
    "version_appli": "app_version",
    "visites": "visits",
    "voient": "member_count",
    "vu": "seen",
    "vue": "view",
    "vus": "seen_items",
}


def convert(value, top_names=False):
    """Renomme les clés connues, à toute profondeur. Si l'ancienne et la nouvelle sont là, la nouvelle gagne."""
    if isinstance(value, list):
        return [convert(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        new_key = key if top_names else RENAMED_KEYS.get(key, key)
        if new_key != key and new_key in value:
            continue
        result[new_key] = item if new_key in NAMES_INSIDE else convert(item)
    return result


def _read(path):
    """Le contenu : un objet (.json) ou la liste des lignes (.jsonl)."""
    with open(path) as file:
        text = file.read()
    if path.endswith(".jsonl"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def _text(path, data):
    if path.endswith(".jsonl"):
        return "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in data)
    return json.dumps(data, ensure_ascii=False)


def convert_file(path, copy_dir):
    """True si le fichier a changé. Un fichier illisible reste tel quel."""
    try:
        data = _read(path)
    except (OSError, ValueError):
        return False
    if path.endswith(".jsonl"):
        new_data = [convert(line) for line in data]
    else:
        new_data = convert(data, os.path.basename(path) in KEYED_BY_NAME)
    if new_data == data:
        return False
    os.makedirs(copy_dir, exist_ok=True)
    copy_path = os.path.join(copy_dir, os.path.basename(path))
    if not os.path.exists(copy_path):
        shutil.copy2(path, copy_path)
    temporary_file = path + ".tmp"
    with open(temporary_file, "w") as file:
        file.write(_text(path, new_data))
    shutil.copymode(path, temporary_file)
    os.replace(temporary_file, path)
    return True


def run(data_dir):
    """Les fichiers convertis (chemins relatifs au dossier des données)."""
    changed = []
    for folder, subfolders, files in os.walk(data_dir):
        subfolders[:] = [name for name in subfolders if name not in SKIPPED_FOLDERS]
        relative = os.path.relpath(folder, data_dir)
        copy_dir = os.path.join(data_dir, COPY_FOLDER, "" if relative == "." else relative)
        for name in sorted(files):
            if name.endswith((".json", ".jsonl")) and convert_file(os.path.join(folder, name), copy_dir):
                changed.append(os.path.normpath(os.path.join(relative, name)))
    return changed


if __name__ == "__main__":
    data = os.environ.get("JABBA_DATA", "/data")
    try:
        done = run(data)
    except Exception as error:  # jamais bloquant : l'appli démarre quand même
        print("Conversion des données impossible :", error, file=sys.stderr)
    else:
        if done:
            print("Données converties (copie dans " + COPY_FOLDER + ") :", ", ".join(done))
