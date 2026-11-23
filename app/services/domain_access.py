"""Accès par le domaine (outil de l'Établi) : Nextcloud joignable depuis Internet, en HTTPS.

Chacun met son propre domaine (rien n'est écrit dans le code) : l'admin le saisit ici.
Seul Nextcloud est ouvert, à l'adresse <préfixe>.<domaine> ; Jabba reste réservé à Tailscale.
Caddy (host/actions.py, « acces_domaine ») reçoit Internet sur le port DOMAIN_PORT du serveur,
vers lequel la box redirige son port 443, et obtient tout seul le certificat HTTPS.

Sommaire :
  read() / save(settings)  : les réglages (domaine, préfixe, actif) dans domaine.json
  address(settings)        : l'adresse publique de Nextcloud (cloud.exemple.fr)
  enable(domain, prefix)   : installe Caddy, prévient Nextcloud de sa nouvelle adresse et y installe
                             la double vérification (codes à 6 chiffres, à activer par chacun)
  disable()                : retire Caddy et l'adresse de Nextcloud
  status(settings)         : la liste de vérification (adresse de la box, DNS, certificat, DNS réglé par
                             Jabba chez Cloudflare ou OVH : services/dns_update.py)
  protection()             : Nextcloud voit-il la vraie adresse de chaque visiteur, et freine-t-il les essais en
                             rafale ? Remet les relais de confiance s'ils manquent ; True | False | None (inconnu)
  watch_protection()       : chaque heure, si l'accès est ouvert : protection() (une alerte si elle reste coupée)
  public_link(url)         : un lien de partage Nextcloud, réécrit sur l'adresse publique
  public_ip       : l'adresse de la box sur Internet (celle que le domaine doit indiquer)
  resolve         : les adresses IPv4 vers lesquelles pointe un nom (ce qu'on a mis dans la zone DNS)
  certificate_ok  : caddy a-t-il obtenu un vrai certificat pour cette adresse ? (connexion locale, certificat vérifié)
  interne : _tell_nextcloud, _forget_in_nextcloud, _offer_two_factor
"""

import os
import socket
import ssl
import urllib.parse

import httpx

import config
from host import action_common, actions_tools, commands
from services import (
    cloudflare_dns,
    dns_update,
    guest_network,
    json_files,
    nextcloud_settings,
    notifications,
    ovh_dns,
    tools,
)

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
SETTINGS_FILE = os.path.join(DATA_DIR, "domaine.json")
DEFAULTS = {"domaine": "", "prefixe": "cloud", "actif": False}
PUBLIC_IP_SERVICE = "https://api.ipify.org"
# Les demandes arrivent de Caddy (sur le serveur) ou d'un pont Docker : Nextcloud peut alors lire
# la vraie adresse de chaque visiteur (sa protection contre les essais en rafale en dépend).
LOCAL_PROXIES = ["127.0.0.1", "172.16.0.0/12"]
# Nextcloud ouvert sur Internet : l'appli officielle des codes à 6 chiffres (Authenticator, Mots de passe d'Apple…).
TWO_FACTOR_APP = "twofactor_totp"


def read():
    return {**DEFAULTS, **json_files.load(SETTINGS_FILE, {})}


def save(settings):
    json_files.save(SETTINGS_FILE, settings)


def address(settings):
    if not settings.get("domaine"):
        return ""
    prefix = settings.get("prefixe") or ""
    return f"{prefix}.{settings['domaine']}" if prefix else settings["domaine"]


def _tell_nextcloud(public_address):
    """Ajoute l'adresse publique aux adresses acceptées par Nextcloud, et Caddy à ses relais de confiance."""
    nextcloud_settings.add_to_system_list("trusted_domains", public_address)
    for proxy in LOCAL_PROXIES:
        nextcloud_settings.add_to_system_list("trusted_proxies", proxy)


def _forget_in_nextcloud(public_address):
    domains = nextcloud_settings.system_list("trusted_domains")
    if public_address not in domains:
        return
    remaining = [domain for domain in domains if domain != public_address]
    commands.occ(["config:system:delete", "trusted_domains"])
    for index, domain in enumerate(remaining):
        commands.occ(["config:system:set", "trusted_domains", str(index), "--value=" + domain])


def _offer_two_factor():
    """Installe (ou réactive) la double vérification dans Nextcloud. Chacun l'active ensuite pour son compte."""
    code, _, _ = commands.occ(["app:install", TWO_FACTOR_APP], timeout=180)
    if code != 0:  # déjà installée, ou boutique d'applis injoignable : on l'active si elle est là
        commands.occ(["app:enable", TWO_FACTOR_APP])


def enable(domain, prefix="cloud"):
    """Vérifie l'adresse, installe Caddy, prévient Nextcloud. Lève ValueError si l'adresse est refusée."""
    domain = action_common.host_name(domain)
    prefix = str(prefix or "").strip().lower()
    if prefix and not action_common.HOST_NAME.fullmatch(prefix + ".exemple.fr"):
        raise ValueError(f"Préfixe refusé : {prefix!r}")
    settings = {**read(), "domaine": domain, "prefixe": prefix}
    public_address = address(settings)
    action_common.host_name(public_address)
    if not config.DEMO:
        code, _, error_output = commands.run_action(
            "acces_domaine", adresse=public_address, port=tools.PORTS["nextcloud"]
        )
        if code != 0:
            raise RuntimeError(error_output.strip()[-300:] or "Caddy n'a pas pu démarrer.")
        _tell_nextcloud(public_address)
        _offer_two_factor()
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


def protection():
    """Derrière Caddy, Nextcloud ne voit que 127.0.0.1 si Caddy n'est pas un relai de confiance : il freinerait
    alors tout le monde d'un coup (ou personne). Les relais manquants sont remis ; la protection coupée
    (auth.bruteforce.protection.enabled = false) ou un en-tête d'adresse changé sont signalés, pas forcés."""
    if config.DEMO:
        return True
    code, _, _ = commands.occ(["status"])
    if code != 0:
        return None  # Nextcloud arrêté ou injoignable : on ne sait pas
    proxies = nextcloud_settings.system_list("trusted_proxies")
    for proxy in LOCAL_PROXIES:
        if proxy not in proxies:
            nextcloud_settings.add_to_system_list("trusted_proxies", proxy)
    _, enabled, _ = commands.occ(["config:system:get", "auth.bruteforce.protection.enabled"])
    headers = nextcloud_settings.system_list("forwarded_for_headers")
    return enabled.strip() != "false" and (not headers or "HTTP_X_FORWARDED_FOR" in headers)


def watch_protection():
    """Chaque heure, si l'accès par le domaine est ouvert : la protection de Nextcloud (alerte si coupée)."""
    settings = read()
    if not settings.get("actif") or protection() is not False:
        return
    notifications.send(
        "securite",
        "Nextcloud : protection contre les essais en rafale coupée",
        "Nextcloud est ouvert sur Internet mais ne freine plus les essais de mots de passe : "
        "Établi → Accès depuis Internet.",
        "/#/outils",
        family="systeme",
    )


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
        with socket.create_connection(("127.0.0.1", actions_tools.DOMAIN_PORT), timeout=5) as raw:
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
        "port_serveur": actions_tools.DOMAIN_PORT,
        "dns": pointing,
        "dns_ok": bool(box_address) and box_address in pointing,
        "certificat_ok": certificate,
        "protection_ok": protection() if settings.get("actif") else None,
        "dns_auto": dns_update.provider(),
        "cloudflare_lien": cloudflare_dns.TOKEN_PAGE,
        "ovh_pret": ovh_dns.is_ready(),
        "ovh_lien": ovh_dns.token_link(settings["domaine"]) if settings.get("domaine") else "",
    }


def public_link(url):
    """Un lien de partage créé par Nextcloud porte l'adresse interne : on y met l'adresse publique."""
    settings = read()
    if not url or not settings.get("actif") or not settings.get("domaine"):
        return url
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(("https", address(settings), parts.path, parts.query, parts.fragment))
