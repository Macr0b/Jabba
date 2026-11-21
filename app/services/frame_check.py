"""Un outil accepte-t-il de s'afficher dans Jabba (dans un cadre, avec la barre de retour) ?

Certains outils l'interdisent exprès (en-têtes X-Frame-Options ou CSP frame-ancestors) : un
gestionnaire de mots de passe se protège ainsi des fausses pages qui piègent les clics. Jabba
regarde ce que l'outil répond sur le serveur lui-même et ne le met dans un cadre que s'il
n'interdit rien ; sinon, l'outil s'ouvre à part. Réponse gardée 10 minutes.

Sommaire :
  allows_framing(port)    : True si l'outil (sur 127.0.0.1:port) accepte d'être affiché dans Jabba
  frameable(headers)      : la règle, sur les en-têtes d'une réponse
  interne : _probe
"""

import threading
import time

import httpx

CACHE_SECONDS = 600
_cache = {}
_LOCK = threading.Lock()


def frameable(headers):
    if headers.get("x-frame-options", "").strip():
        return False  # DENY ou SAMEORIGIN : Jabba est sur un autre port, donc une autre origine
    for rule in headers.get("content-security-policy", "").split(";"):
        parts = rule.split()
        if parts and parts[0].lower() == "frame-ancestors":
            return "*" in parts[1:]
    return True


def _probe(port):
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/", follow_redirects=True, timeout=2)
    except httpx.HTTPError:
        return False
    return frameable(response.headers)


def allows_framing(port):
    if not port:
        return False
    with _LOCK:
        known = _cache.get(port)
        if known and time.monotonic() - known[1] < CACHE_SECONDS:
            return known[0]
    answer = _probe(port)
    with _LOCK:
        _cache[port] = (answer, time.monotonic())
    return answer
