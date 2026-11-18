"""Relais des alertes vers ntfy (outil de l'Établi) : elles arrivent sur le téléphone par l'appli ntfy.

ntfy écoute sur le serveur lui-même (127.0.0.1:2586, l'appli tourne sur le réseau de l'hôte).
Les alertes vont dans un sujet tiré au sort une fois pour toutes : il faut le connaître pour s'y abonner.
Les alertes réservées à certains comptes ne passent pas par ntfy (un sujet est commun à tous).

Sommaire :
  topic()                       : le sujet (créé au premier appel, gardé dans /data)
  is_running()                  : ntfy répond-il sur le serveur ?
  forward(title, text, url)     : envoi en arrière-plan ; sans ntfy, rien ne se passe
"""

import os
import secrets
import socket
import threading

import httpx

from services import json_files

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
TOPIC_FILE = os.path.join(DATA_DIR, "ntfy.json")
ADDRESS = ("127.0.0.1", 2586)
PRIORITIES = {"securite": "high", "courant": "high", "disque_plein": "high"}
_LOCK = threading.Lock()


def topic():
    with _LOCK:
        saved = json_files.load(TOPIC_FILE, {})
        if not saved.get("sujet"):
            saved = {"sujet": "jabba-" + secrets.token_hex(8)}
            json_files.save(TOPIC_FILE, saved, private=True)
        return saved["sujet"]


def is_running():
    try:
        with socket.create_connection(ADDRESS, timeout=0.3):
            return True
    except OSError:
        return False


def _post(media_type, title, text, url):
    headers = {"Title": title.encode("utf-8"), "Priority": PRIORITIES.get(media_type, "default"), "Tags": "jabba"}
    if url.startswith("https://"):
        headers["Click"] = url
    try:
        httpx.post(
            f"http://{ADDRESS[0]}:{ADDRESS[1]}/{topic()}",
            content=(text or title).encode("utf-8"),
            headers=headers,
            timeout=5,
        )
    except httpx.HTTPError:
        pass  # ntfy pas installé ou arrêté : les autres alertes partent quand même


def forward(media_type, title, text, url="/"):
    threading.Thread(target=_post, args=(media_type, title, text, url), daemon=True).start()
