"""Accès par le domaine (outil de l'Établi) : Nextcloud joignable depuis Internet, en HTTPS.

Chacun met son propre domaine (rien n'est écrit dans le code) : l'admin le saisit ici.
Seul Nextcloud est ouvert, à l'adresse <préfixe>.<domaine> ; Jabba reste réservé à Tailscale.
Caddy (host/actions.py, « acces_domaine ») reçoit Internet sur le port DOMAIN_PORT du serveur,
vers lequel la box redirige son port 443, et obtient tout seul le certificat HTTPS.

Sommaire :
  read() / save(settings)  : les réglages (domaine, préfixe, actif) dans domaine.json
  address(settings)        : l'adresse publique de Nextcloud (cloud.exemple.fr)
  enable(domain, prefix)   : installe Caddy et prévient Nextcloud de sa nouvelle adresse
  disable()                : retire Caddy et l'adresse de Nextcloud
  status(settings)         : la liste de vérification (adresse de la box, DNS, certificat)
  public_link(url)         : un lien de partage Nextcloud, réécrit sur l'adresse publique
"""

import os
import socket
import ssl
import urllib.parse

import httpx

import config
from host import actions, commands
from services import guest_network, json_files, tools

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
SETTINGS_FILE = os.path.join(DATA_DIR, "domaine.json")
DEFAULTS = {"domaine": "", "prefixe": "cloud", "actif": False}
PUBLIC_IP_SERVICE = "https://api.ipify.org"
# Les demandes arrivent de Caddy (sur le serveur) ou d'un pont Docker : Nextcloud peut alors lire
# la vraie adresse de chaque visiteur (sa protection contre les essais en rafale en dépend).
LOCAL_PROXIES = ["127.0.0.1", "172.16.0.0/12"]


def read():
    return {**DEFAULTS, **json_files.load(SETTINGS_FILE, {})}


def save(settings):
    json_files.save(SETTINGS_FILE, settings)


def address(settings):
    if not settings.get("domaine"):
        return ""
    prefix = settings.get("prefixe") or ""
    return f"{prefix}.{settings['domaine']}" if prefix else settings["domaine"]


def _system_list(key):
    """Une liste des réglages de Nextcloud (une valeur par ligne), vide si absente."""
    code, output, _ = commands.occ(["config:system:get", key])
    return [line.strip() for line in output.splitlines() if line.strip()] if code == 0 else []


def _add_to_system_list(key, value):
    values = _system_list(key)
    if value not in values:
        commands.occ(["config:system:set", key, str(len(values)), "--value=" + value])


def _tell_nextcloud(public_address):
    """Ajoute l'adresse publique aux adresses acceptées par Nextcloud, et Caddy à ses relais de confiance."""
    _add_to_system_list("trusted_domains", public_address)
    for proxy in LOCAL_PROXIES:
        _add_to_system_list("trusted_proxies", proxy)


def _forget_in_nextcloud(public_address):
    domains = _system_list("trusted_domains")
    if public_address not in domains:
        return
    remaining = [domain for domain in domains if domain != public_address]
    commands.occ(["config:system:delete", "trusted_domains"])
    for index, domain in enumerate(remaining):
        commands.occ(["config:system:set", "trusted_domains", str(index), "--value=" + domain])


def enable(domain, prefix="cloud"):
    """Vérifie l'adresse, installe Caddy, prévient Nextcloud. Lève ValueError si l'adresse est refusée."""
    domain = actions.host_name(domain)
    prefix = str(prefix or "").strip().lower()
    if prefix and not actions.HOST_NAME.fullmatch(prefix + ".exemple.fr"):
        raise ValueError(f"Préfixe refusé : {prefix!r}")
    settings = {**read(), "domaine": domain, "prefixe": prefix}
    public_address = address(settings)
    actions.host_name(public_address)
    if not config.DEMO:
        code, _, error_output = commands.run_action(
            "acces_domaine", adresse=public_address, port=tools.PORTS["nextcloud"]
        )
        if code != 0:
            raise RuntimeError(error_output.strip()[-300:] or "Caddy n'a pas pu démarrer.")
        _tell_nextcloud(public_address)
    settings["actif"] = True
    save(settings)
    return settings


def disable():
    settings = read()
    if not config.DEMO:
        commands.run_action("retirer_acces_domaine")
        if settings.get("domaine"):
            _forget_in_nextcloud(address(settings))
    settings["actif"] = False
    save(settings)
    return settings


def public_ip():
    """L'adresse de la box sur Internet (celle que le domaine doit indiquer)."""
    if config.DEMO:
        return "203.0.113.7"
    try:
        response = httpx.get(PUBLIC_IP_SERVICE, timeout=10)
        response.raise_for_status()
        return response.text.strip()
    except httpx.HTTPError:
        return ""


def resolve(name):
    """Les adresses IPv4 vers lesquelles pointe un nom (ce qu'on a mis dans la zone DNS)."""
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(name, 443, socket.AF_INET, socket.SOCK_STREAM)})
    except (socket.gaierror, UnicodeError):
        return []


def certificate_ok(name):
    """Caddy a-t-il obtenu un vrai certificat pour cette adresse ? (connexion locale, certificat vérifié)"""
    context = ssl.create_default_context()
    try:
        with socket.create_connection(("127.0.0.1", actions.DOMAIN_PORT), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname=name):
                return True
    except (OSError, ssl.SSLError):
        return False


def status(settings=None):
    """La liste de vérification affichée dans l'Établi, dans l'ordre où l'admin s'en occupe."""
    settings = settings or read()
    public_address = address(settings)
    _, lan_address = guest_network.wifi_address()
    box_address = public_ip()
    if config.DEMO:
        pointing = [box_address] if settings.get("actif") else []
        certificate = bool(settings.get("actif"))
    else:
        pointing = resolve(public_address) if public_address else []
        certificate = bool(settings.get("actif")) and certificate_ok(public_address)
    return {
        **settings,
        "adresse": public_address,
        "ip_box": box_address,
        "ip_serveur": lan_address or "",
        "port_serveur": actions.DOMAIN_PORT,
        "dns": pointing,
        "dns_ok": bool(box_address) and box_address in pointing,
        "certificat_ok": certificate,
    }


def public_link(url):
    """Un lien de partage créé par Nextcloud porte l'adresse interne : on y met l'adresse publique."""
    settings = read()
    if not url or not settings.get("actif") or not settings.get("domaine"):
        return url
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(("https", address(settings), parts.path, parts.query, parts.fragment))
