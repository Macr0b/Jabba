"""AdGuard Home piloté depuis Jabba : le bloqueur de pubs s'allume appareil par appareil.

Par défaut, aucun appareil n'est filtré (le filtrage général est coupé) : un téléphone qui arrive
sur le Wi-Fi passe sans filtre. Jabba allume le filtrage d'un appareil en le déclarant « client »
d'AdGuard, avec ses propres réglages (filtrage actif) ; l'éteindre retire ce client.
La première fois, Jabba fait lui-même l'installation d'AdGuard (à la place de l'assistant), avec
le compte « admin » et le mot de passe tiré au sort à l'activation (services/tool_credentials.py).

AdGuard écoute sur le serveur (127.0.0.1:3000) ; l'appli tourne sur le réseau de l'hôte.

Sommaire :
  devices()                      : {"pret", "message", "appareils": [{ip, nom, protege, tailscale}]}
  set_protected(ip, protected)   : allume / éteint le filtrage d'un appareil
  interne : _client, _ensure_ready, _tailscale_names, _client_name, _demo
"""

import ipaddress
import json
import threading
import time

import httpx

import config
from host import commands
from services import tool_credentials

ADDRESS = "http://127.0.0.1:3000"
ADMIN = "admin"
CLIENT_PREFIX = "Jabba "
_LOCK = threading.Lock()
_DEMO_PROTECTED = {"100.64.0.2"}


class AdBlockerError(Exception):
    pass


def _password():
    return (tool_credentials.read("adguard-home") or {}).get("mot_de_passe", "")


def _client():
    return httpx.Client(base_url=ADDRESS, auth=(ADMIN, _password()), timeout=5)


def _ensure_ready(client):
    """Fait l'installation d'AdGuard s'il attend encore son assistant, puis coupe le filtrage général."""
    try:
        first_run = client.get("/control/install/get_addresses").status_code == 200
    except httpx.HTTPError:
        raise AdBlockerError("AdGuard Home ne répond pas : il démarre peut-être encore.")
    if first_run:
        if not _password():
            raise AdBlockerError("Mot de passe d'AdGuard inconnu : retire puis rallume AdGuard dans l'Établi.")
        listen = {"ip": "0.0.0.0", "port": 3000}
        setup = {"web": listen, "dns": {"ip": "0.0.0.0", "port": 53}, "username": ADMIN, "password": _password()}
        if client.post("/control/install/configure", json=setup).status_code != 200:
            raise AdBlockerError("AdGuard Home a refusé son installation.")
        for _ in range(20):  # AdGuard redémarre sa page après l'installation
            try:
                if client.get("/control/status").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        client.post("/control/filtering/config", json={"enabled": False, "interval": 24})
    status = client.get("/control/status")
    if status.status_code in (401, 403):
        raise AdBlockerError("Mot de passe d'AdGuard refusé : retire puis rallume AdGuard dans l'Établi.")
    if status.status_code != 200:
        raise AdBlockerError("AdGuard Home ne répond pas comme prévu.")


def _tailscale_names():
    """Adresse Tailscale → nom de l'appareil (les appareils Tailscale n'ont pas de nom pour AdGuard)."""
    code, output, _ = commands.run_action("appareils_tailscale")
    if code != 0:
        return {}
    try:
        peers = (json.loads(output).get("Peer") or {}).values()
    except ValueError:
        return {}
    return {address: peer.get("HostName") or address for peer in peers for address in peer.get("TailscaleIPs") or []}


def _client_name(ip):
    return CLIENT_PREFIX + ip


def _demo():
    seen = [
        ("192.168.1.20", "iPhone-de-Rob", False),
        ("192.168.1.31", "television", False),
        ("100.64.0.2", "macbook", True),
    ]
    return {
        "pret": True,
        "message": "",
        "appareils": [
            {"ip": ip, "nom": name, "protege": ip in _DEMO_PROTECTED, "tailscale": tailscale}
            for ip, name, tailscale in seen
        ],
    }


def devices():
    if config.DEMO:
        return _demo()
    try:
        with _client() as client:
            _ensure_ready(client)
            data = client.get("/control/clients").json()
    except (AdBlockerError, httpx.HTTPError, ValueError) as error:
        return {"pret": False, "message": str(error) or "AdGuard Home ne répond pas.", "appareils": []}
    names = _tailscale_names()
    protected = set()
    found = {}
    for known in data.get("clients") or []:
        if not str(known.get("name", "")).startswith(CLIENT_PREFIX):
            continue
        for ip in known.get("ids") or []:
            if known.get("filtering_enabled") and not known.get("use_global_settings"):
                protected.add(ip)
            found.setdefault(ip, "")
    for seen in data.get("auto_clients") or []:
        if seen.get("ip"):
            found[seen["ip"]] = seen.get("name") or found.get(seen["ip"], "")
    listed = []
    for ip, name in found.items():
        listed.append(
            {
                "ip": ip,
                "nom": names.get(ip) or name or ip,
                "protege": ip in protected,
                "tailscale": ip in names,
            }
        )
    listed.sort(key=lambda device: (not device["protege"], device["nom"].lower()))
    return {"pret": True, "message": "", "appareils": listed}


def set_protected(ip, protected):
    try:
        ip = str(ipaddress.ip_address(str(ip)))
    except ValueError:
        raise AdBlockerError(f"Adresse refusée : {ip!r}")
    if config.DEMO:
        (_DEMO_PROTECTED.add if protected else _DEMO_PROTECTED.discard)(ip)
        return
    settings = {
        "name": _client_name(ip),
        "ids": [ip],
        "use_global_settings": False,
        "filtering_enabled": True,
        "parental_enabled": False,
        "safebrowsing_enabled": False,
        "safe_search": {"enabled": False},
        "use_global_blocked_services": True,
        "tags": [],
    }
    with _LOCK, _client() as client:
        _ensure_ready(client)
        client.post("/control/clients/delete", json={"name": _client_name(ip)})  # repartir propre
        if protected and client.post("/control/clients/add", json=settings).status_code != 200:
            raise AdBlockerError("AdGuard Home a refusé le réglage de cet appareil.")
