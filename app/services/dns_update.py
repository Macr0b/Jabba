"""L'entrée DNS du domaine tenue à jour par Jabba, chez le fournisseur du DNS : Cloudflare ou OVH.

Un seul fournisseur à la fois : enregistrer un jeton Cloudflare fait oublier la clé OVH (le DNS a
déménagé ; la clé reste à révoquer chez OVH, Système → Mes codes le rappelle). Chaque heure, si l'adresse
de la box a changé, l'entrée suit et une alerte le dit.

Sommaire :
  provider()           : "cloudflare", "ovh" ou None (personne ne règle le DNS)
  sync_now(settings)   : règle l'entrée tout de suite ; "cree" | "corrige" | "deja" | None (rien à faire)
  watch(now)           : chaque heure, si l'accès par le domaine est actif
  ERRORS               : les refus possibles des deux fournisseurs
"""

import time

from services import cloudflare_dns, notifications, ovh_dns

WATCH_SECONDS = 3600
ERRORS = (cloudflare_dns.CloudflareError, ovh_dns.OvhError)
NAMES = {"cloudflare": "Cloudflare", "ovh": "OVH"}
_STATE = {"dernier": 0.0}


def provider():
    if cloudflare_dns.is_ready():
        return "cloudflare"
    if ovh_dns.is_ready():
        return "ovh"
    return None


def sync_now(settings):
    from services import domain_access  # évite l'import circulaire (domain_access se sert de ce module)

    chosen = provider()
    if not chosen or not settings.get("actif") or not settings.get("domaine"):
        return None
    module = cloudflare_dns if chosen == "cloudflare" else ovh_dns
    return module.sync(settings["domaine"], settings.get("prefixe") or "cloud", domain_access.public_ip())


def watch(now=None):
    """Chaque heure : si l'adresse de la box a changé, l'entrée DNS suit (une alerte le dit)."""
    from services import domain_access

    now = now or time.time()
    if now - _STATE["dernier"] < WATCH_SECONDS or not provider():
        return None
    _STATE["dernier"] = now
    try:
        result = sync_now(domain_access.read())
    except ERRORS as error:
        notifications.send("securite", "Domaine : DNS pas à jour", str(error), "/#/outils", family="systeme")
        return None
    if result == "corrige":
        notifications.send(
            "mises_a_jour",
            "Domaine : adresse de la box changée",
            f"Jabba a corrigé l'entrée DNS chez {NAMES[provider()]} ; le domaine suit dans l'heure.",
            "/#/outils",
            family="systeme",
        )
    return result
