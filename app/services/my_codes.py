"""« Mes codes » : la liste de tous les codes qui existent autour du serveur, à quoi ils servent et où
ils sont rangés. Aucun code n'est jamais montré ici : seulement s'il existe, et quoi en faire.

Trois rangements :
  - "trousseau" : à garder dans ton gestionnaire de mots de passe (et dans ta tête pour le premier) ;
  - "jabba"     : gardé par Jabba (menu … → Identifiants, ou dans ses réglages) : rien à retenir ;
  - "supprimer" : un code de passage, qui ne sert plus une fois utilisé : il peut quitter ton trousseau.

Sommaire :
  inventory(installed, identifier) : la liste, dans l'ordre d'affichage
  interne : _to_keep, _tools_to_keep, _tool_entries, _service_keys, _to_delete, _entry
"""

import config
from host import catalog
from security import second_factor, server_password
from services import backups, cloudflare_dns, heartbeat, ovh_dns, tool_addresses, tool_credentials


def _entry(name, purpose, place, present=True, note=""):
    return {"nom": name, "sert_a": purpose, "rangement": place, "present": present, "note": note}


def _tool_entries(installed):
    entries = []
    for tool in sorted(installed & tool_credentials.STORABLE - {"nextcloud", "time-machine"}):
        name = catalog.OPTIONAL_TOOLS.get(tool, {}).get("nom") or {"jellyfin": "Jellyfin", "dockge": "Dockge"}[tool]
        login = tool_credentials.read(tool)
        if tool_credentials.created_by_jabba(tool):
            note = "Créé par Jabba." if login else "Installé avant la 0.16.2 : mot de passe tapé à l'activation."
        elif login:
            note = "Noté dans Jabba."
        else:
            note = "Pas encore noté : carte de l'outil → … → Identifiants."
        entries.append(
            _entry(
                f"Compte {name}",
                f"Te connecter à {name}.",
                "jabba",
                present=bool(login),
                note=note + " Safari peut aussi le retenir, à l'adresse de l'outil.",
            )
        )
    return entries


def _to_keep(installed, identifier):
    """« trousseau » : ce que toi seul peux garder."""
    passkey = second_factor.summary(identifier)
    codes = [
        _entry(
            "Mot de passe du serveur",
            "Entrer dans Jabba (super admin) et sur le serveur en SSH.",
            "trousseau",
            note="Le seul à connaître par cœur.",
        ),
        _entry(
            "Code de secours de Jabba (6 mots)",
            "Choisir un nouveau mot de passe si tu oublies celui du serveur.",
            "trousseau",
            present=server_password.recovery_code_exists(),
            note="À créer dans Système → Code de secours." if not server_password.recovery_code_exists() else "",
        ),
        _entry(
            "Double vérification de Jabba",
            "Le second facteur de ta connexion admin (appli de codes, clé d'accès) et ses 10 codes de secours.",
            "trousseau",
            present=passkey["active"],
            note=f"{passkey['codes_restants']} codes de secours restants." if passkey["active"] else "Pas activée.",
        ),
    ]
    return codes + _tools_to_keep(installed)


def _tools_to_keep(installed):
    """« trousseau » aussi : Nextcloud, la phrase des sauvegardes, Time Machine."""
    codes = []
    if "nextcloud" in installed:
        codes.append(
            _entry(
                "Mot de passe Nextcloud",
                "Tes fichiers, sur le web et dans les applis Nextcloud (avec le code de ton appli Authenticator).",
                "trousseau",
            )
        )
    if backups.read().get("chemin"):
        codes.append(
            _entry(
                "Phrase de passe des sauvegardes",
                "Relire les sauvegardes chiffrées. Sans elle, personne ne peut les relire, toi non plus.",
                "trousseau",
                present=bool(backups.passphrase()),
                note="Garde-la hors du serveur : c'est elle qui te sauve s'il tombe en panne.",
            )
        )
    if "time-machine" in installed:
        codes.append(
            _entry(
                "Mot de passe Time Machine (partage Samba)",
                "Le Mac s'en sert seul, à chaque sauvegarde.",
                "trousseau",
                present=bool(tool_credentials.read("time-machine")),
                note="Ne le supprime pas du Mac. L'original est dans Jabba : carte Time Machine → … → Identifiants.",
            )
        )
    return codes


def _service_keys():
    """« jabba » : les clés des services (Nextcloud, healthchecks.io, DNS, Tailscale), gardées par Jabba."""
    codes = []
    if config.NC_APP_PASSWORD:
        codes.append(
            _entry(
                "Mot de passe d'application « Appli Jabba » (Nextcloud)",
                "Permet à Jabba de montrer tes dossiers Nextcloud (écran Fichiers).",
                "jabba",
                note="Révocable dans Nextcloud → Paramètres personnels → Sécurité.",
            )
        )
    if heartbeat.status()["pret"]:
        codes.append(
            _entry(
                "Adresse de signal (healthchecks.io)",
                "Le signal de vie que Jabba envoie toutes les 5 minutes ; c'est le service qui te prévient du silence.",
                "jabba",
                note="Garde surtout ton compte healthchecks.io dans ton trousseau.",
            )
        )
    if cloudflare_dns.is_ready():
        codes.append(
            _entry(
                "Jeton Cloudflare",
                "Tenir à jour l'entrée DNS de ton domaine (droits limités à « Zone → DNS → Modifier » sur ce domaine).",
                "jabba",
                note="Révocable dans Jabba (Accès depuis Internet → Oublier le jeton) et chez Cloudflare.",
            )
        )
    if ovh_dns.is_ready():
        codes.append(
            _entry(
                "Clé OVH",
                "Tenir à jour l'entrée DNS de ton domaine (droits limités à sa zone DNS).",
                "jabba",
                note="Révocable dans Jabba (Accès depuis Internet → Oublier la clé) et chez OVH.",
            )
        )
    if tool_addresses.status(set())["cle_prete"]:
        codes.append(
            _entry(
                "Clé d'accès Tailscale (tskey-api-…)",
                "Donner une adresse à part à chaque outil que tu actives.",
                "jabba",
                note="Gardée jusqu'à son expiration (90 jours au plus), lisible par l'appli seule. Révocable : "
                "console Tailscale → Settings → Keys.",
            )
        )
    return codes


def _to_delete():
    """« supprimer » : les codes de passage, qui ne servent plus une fois utilisés."""
    codes = []
    if cloudflare_dns.is_ready():
        codes.append(
            _entry(
                "Ancienne clé OVH (application key, secret, consumer key)",
                "Le DNS est maintenant chez Cloudflare : Jabba l'a oubliée.",
                "supprimer",
                note="Révoque-la aussi chez OVH : api.ovh.com/console → /me/api/application → supprime l'application.",
            )
        )
    codes += [
        _entry(
            "Mots de passe provisoires (3 mots et un nombre)",
            "Première connexion d'un compte admin créé dans Comptes : valables 24 h, à changer aussitôt.",
            "supprimer",
        ),
        _entry(
            "Clés d'inscription Tailscale (tskey-auth-…)",
            "Inscrire une machine une fois ; elles expirent ensuite.",
            "supprimer",
            note="Si l'une n'a pas expiré : console Tailscale → Settings → Keys → Revoke.",
        ),
        _entry(
            "Mots de passe d'outils rangés sous l'adresse du serveur (avec un :port)",
            "Les anciennes adresses des outils, que le trousseau confondait.",
            "supprimer",
            note="Note-les d'abord dans Jabba (… → Identifiants). Garde celui de Jabba.",
        ),
    ]
    return codes


def inventory(installed, identifier):
    """La liste, dans l'ordre d'affichage : à garder, gardés par Jabba, à supprimer."""
    installed = set(installed)
    return _to_keep(installed, identifier) + _tool_entries(installed) + _service_keys() + _to_delete()
