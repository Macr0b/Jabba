"""L'entrée DNS du domaine réglée par Jabba chez Cloudflare (API de Cloudflare), puis tenue à jour.

L'admin crée une fois chez Cloudflare un jeton d'API limité à « Zone → DNS → Modifier » sur son seul
domaine (modèle « Modifier la zone DNS », jamais la clé globale), et le colle dans l'Établi. Jabba ne
touche qu'à UNE entrée : la ligne A « <préfixe> » (cloud.exemple.fr), qu'il crée si elle manque (sans
proxy : nuage gris, le certificat HTTPS se fait sur le serveur) et dont il corrige l'adresse si celle de
la box change. Les autres entrées du domaine ne sont jamais lues pour être changées, ni supprimées ; le
proxy d'une entrée existante est laissé tel quel. Le jeton est gardé dans un fichier lisible par
l'appli seule et ne repart jamais vers l'écran.

Sommaire :
  TOKEN_PAGE                 : la page de Cloudflare où créer le jeton
  is_ready()                 : un jeton est enregistré
  save_token(zone, token)    : vérifie le jeton (il voit ce domaine, et lui seul), puis le garde
  forget()                   : oublie le jeton
  sync(zone, prefix, ip)     : crée ou corrige l'entrée A ; "cree" | "corrige" | "deja"
  interne : _keys, _call, _records
  CloudflareError : le refus de Cloudflare, avec sa phrase
"""

import os
import re
import threading

import httpx

from services import activity_log, json_files

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
KEYS_FILE = os.path.join(DATA_DIR, "cloudflare.json")
API = "https://api.cloudflare.com/client/v4"
TOKEN_PAGE = "https://dash.cloudflare.com/profile/api-tokens"
TOKEN = re.compile(r"[A-Za-z0-9_-]{30,200}")
ZONE = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
_LOCK = threading.Lock()


class CloudflareError(Exception):
    pass


def _keys():
    return json_files.load(KEYS_FILE, {})


def is_ready():
    return bool(_keys().get("jeton"))


def _call(method, path, payload=None, params=None, token=None):
    token = token or _keys().get("jeton")
    if not token:
        raise CloudflareError("Pas de jeton Cloudflare enregistré.")
    try:
        response = httpx.request(
            method,
            API + path,
            json=payload,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        body = response.json() if response.content else {}
    except (httpx.HTTPError, ValueError):
        raise CloudflareError("Cloudflare ne répond pas : réessaie dans un moment.")
    if response.status_code in (400, 401, 403) and not body.get("success"):
        raise CloudflareError(
            "Cloudflare refuse le jeton : crée-le avec le modèle « Modifier la zone DNS », limité à ton domaine."
        )
    if response.status_code >= 400 or not body.get("success", False):
        raise CloudflareError(f"Cloudflare a refusé la demande ({response.status_code}).")
    return body.get("result")


def _records(zone_id, name, kind, token=None):
    return _call("GET", f"/zones/{zone_id}/dns_records", params={"type": kind, "name": name}, token=token) or []


def save_token(zone, token):
    """Le jeton doit voir ce domaine et aucun autre (« Zone → DNS → Modifier », sur ce domaine seulement)."""
    if not ZONE.fullmatch(zone or ""):
        raise CloudflareError("Indique d'abord ton domaine.")
    token = str(token or "").strip()
    if not TOKEN.fullmatch(token):
        raise CloudflareError(
            "Un jeton Cloudflare est une longue suite de lettres et de chiffres : recopie-le en entier."
        )
    zones = _call("GET", "/zones", params={"per_page": 50}, token=token) or []
    names = {item.get("name") for item in zones}
    if zone not in names:
        raise CloudflareError(f"Ce jeton n'a pas accès à {zone} : choisis ce domaine dans « Ressources de zone ».")
    if len(names) > 1:
        raise CloudflareError(
            f"Ce jeton ouvre aussi d'autres domaines : limite-le à {zone} seul (« Ressources de zone » → Spécifique)."
        )
    zone_id = next(item["id"] for item in zones if item.get("name") == zone)
    _records(zone_id, f"cloud.{zone}", "A", token=token)  # le jeton doit pouvoir lire les entrées DNS
    json_files.save(KEYS_FILE, {"jeton": token, "zone": zone, "zone_id": zone_id}, private=True)


def forget():
    json_files.save(KEYS_FILE, {}, private=True)


def sync(zone, prefix, ip):
    """L'entrée A <préfixe>.<zone> doit indiquer ip. Rien d'autre n'est touché ; un CNAME du même nom bloque."""
    if not ip:
        raise CloudflareError("Adresse de la box inconnue pour l'instant.")
    keys = _keys()
    if keys.get("zone") != zone:
        raise CloudflareError(f"Le jeton enregistré est celui de {keys.get('zone')} : colle celui de {zone}.")
    name = f"{prefix}.{zone}" if prefix else zone
    zone_id = keys["zone_id"]
    with _LOCK:
        if _records(zone_id, name, "CNAME"):
            raise CloudflareError(f"Une entrée CNAME « {name} » existe chez Cloudflare : retire-la, puis réessaie.")
        found = _records(zone_id, name, "A")
        if len(found) == 1 and found[0].get("content") == ip:
            return "deja"
        if found:
            for record in found:  # deux entrées A pour le même nom : la box n'a qu'une adresse
                _call("PATCH", f"/zones/{zone_id}/dns_records/{record['id']}", {"content": ip})
            result = "corrige"
        else:
            entry = {"type": "A", "name": name, "content": ip, "ttl": 1, "proxied": False, "comment": "Jabba"}
            _call("POST", f"/zones/{zone_id}/dns_records", entry)
            result = "cree"
    activity_log.record("systeme", f"DNS chez Cloudflare : {name} → {ip} ({result})")
    return result
