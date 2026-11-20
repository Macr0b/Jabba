"""L'entrée DNS du domaine réglée par Jabba chez OVH (API d'OVH), puis tenue à jour toute seule.

L'admin crée une fois chez OVH une clé limitée à la zone DNS de son domaine (lien préparé par
Jabba, droits lecture / création / modification sur /domain/zone/<domaine>/*, rien d'autre), et colle
les trois codes dans l'Établi. Jabba crée alors l'entrée A « <préfixe> » vers l'adresse de la box,
et la corrige si cette adresse change (chaque heure). Les codes sont gardés dans un fichier
lisible par l'appli seule et ne repartent jamais vers l'écran.

Sommaire :
  token_link(zone)                 : la page OVH qui crée la clé, droits déjà remplis
  is_ready()                       : une clé est enregistrée
  save_keys(zone, ak, as_, ck)     : vérifie la clé sur la zone, puis l'enregistre
  forget()                         : oublie la clé
  sync(zone, prefix, ip)           : crée ou corrige l'entrée A ; "cree" | "corrige" | "deja"
  watch()                          : chaque heure, si l'accès par le domaine est actif
  interne : _call, _signature, _records
"""

import hashlib
import json
import os
import re
import threading
import time
import urllib.parse

import httpx

from services import activity_log, json_files, notifications

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
KEYS_FILE = os.path.join(DATA_DIR, "ovh.json")
API = "https://eu.api.ovh.com/1.0"
TOKEN_PAGE = "https://eu.api.ovh.com/createToken/"
KEY = re.compile(r"[A-Za-z0-9]{8,64}")
ZONE = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
WATCH_SECONDS = 3600
_STATE = {"dernier": 0.0}
_LOCK = threading.Lock()


class OvhError(Exception):
    pass


def token_link(zone):
    rights = f"/domain/zone/{zone}/*"
    query = urllib.parse.urlencode({"GET": rights, "POST": rights, "PUT": rights})
    return f"{TOKEN_PAGE}?{query}"


def _keys():
    return json_files.load(KEYS_FILE, {})


def is_ready():
    return bool(_keys().get("ck"))


def _signature(secret, consumer, method, url, body, timestamp):
    raw = "+".join([secret, consumer, method, url, body, str(timestamp)])
    return "$1$" + hashlib.sha1(raw.encode()).hexdigest()


def _call(method, path, payload=None, keys=None):
    keys = keys or _keys()
    if not keys.get("ck"):
        raise OvhError("Pas de clé OVH enregistrée.")
    url = API + path
    body = json.dumps(payload) if payload is not None else ""
    try:
        timestamp = int(httpx.get(API + "/auth/time", timeout=10).text)
        headers = {
            "X-Ovh-Application": keys["ak"],
            "X-Ovh-Consumer": keys["ck"],
            "X-Ovh-Timestamp": str(timestamp),
            "X-Ovh-Signature": _signature(keys["as"], keys["ck"], method, url, body, timestamp),
            "Content-Type": "application/json",
        }
        response = httpx.request(method, url, content=body or None, headers=headers, timeout=15)
    except (httpx.HTTPError, ValueError):
        raise OvhError("OVH ne répond pas : réessaie dans un moment.")
    if response.status_code in (401, 403):
        raise OvhError("OVH refuse la clé : recrée-la avec le lien de Jabba (droits sur la zone DNS du domaine).")
    if response.status_code >= 400:
        raise OvhError(f"OVH a refusé la demande ({response.status_code}).")
    return response.json() if response.content else None


def _records(zone, prefix, kind, keys=None):
    query = urllib.parse.urlencode({"fieldType": kind, "subDomain": prefix})
    return _call("GET", f"/domain/zone/{zone}/record?{query}", keys=keys) or []


def save_keys(zone, application_key, application_secret, consumer_key):
    if not ZONE.fullmatch(zone or ""):
        raise OvhError("Indique d'abord ton domaine.")
    keys = {"ak": application_key, "as": application_secret, "ck": consumer_key}
    if not all(isinstance(value, str) and KEY.fullmatch(value.strip()) for value in keys.values()):
        raise OvhError("Les trois codes d'OVH sont des suites de lettres et de chiffres : recopie-les en entier.")
    keys = {name: value.strip() for name, value in keys.items()}
    _records(zone, "cloud", "A", keys=keys)  # la clé doit pouvoir lire la zone de ce domaine
    json_files.save(KEYS_FILE, keys, private=True)


def forget():
    json_files.save(KEYS_FILE, {}, private=True)


def sync(zone, prefix, ip):
    """L'entrée A <préfixe>.<zone> doit indiquer ip. Une entrée CNAME du même nom n'est jamais retirée d'office."""
    if not ip:
        raise OvhError("Adresse de la box inconnue pour l'instant.")
    with _LOCK:
        if _records(zone, prefix, "CNAME"):
            raise OvhError(
                f"Une entrée CNAME « {prefix} » existe chez OVH : retire-la dans la zone DNS, puis réessaie."
            )
        found = [_call("GET", f"/domain/zone/{zone}/record/{record}") for record in _records(zone, prefix, "A")]
        if any(record.get("target") == ip for record in found) and len(found) == 1:
            return "deja"
        if found:
            _call("PUT", f"/domain/zone/{zone}/record/{found[0]['id']}", {"target": ip})
            for extra in found[1:]:  # deux entrées A pour le même nom : la box n'en a qu'une adresse
                _call("PUT", f"/domain/zone/{zone}/record/{extra['id']}", {"target": ip})
            result = "corrige"
        else:
            _call(
                "POST", f"/domain/zone/{zone}/record", {"fieldType": "A", "subDomain": prefix, "target": ip, "ttl": 0}
            )
            result = "cree"
        _call("POST", f"/domain/zone/{zone}/refresh")
    activity_log.record("systeme", f"DNS chez OVH : {prefix}.{zone} → {ip} ({result})")
    return result


def watch(now=None):
    """Chaque heure : si l'adresse de la box a changé, l'entrée DNS suit (une alerte le dit)."""
    from services import domain_access  # évite l'import circulaire (domain_access se sert de ce module)

    now = now or time.time()
    if now - _STATE["dernier"] < WATCH_SECONDS or not is_ready():
        return None
    _STATE["dernier"] = now
    settings = domain_access.read()
    if not settings.get("actif") or not settings.get("domaine"):
        return None
    try:
        result = sync(settings["domaine"], settings.get("prefixe") or "cloud", domain_access.public_ip())
    except OvhError as error:
        notifications.send("securite", "Domaine : DNS pas à jour", str(error), "/#/outils", family="systeme")
        return None
    if result == "corrige":
        notifications.send(
            "mises_a_jour",
            "Domaine : adresse de la box changée",
            "Jabba a corrigé l'entrée DNS chez OVH ; le domaine suit dans l'heure.",
            "/#/outils",
            family="systeme",
        )
    return result
