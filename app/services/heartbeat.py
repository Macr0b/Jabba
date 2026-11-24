"""Être prévenu quand le serveur ne répond plus : un service extérieur (healthchecks.io, gratuit) attend
un signe de vie toutes les 5 minutes, et prévient par mail ou par son appli s'il n'en reçoit plus.

Les alertes de Jabba partent du serveur lui-même : s'il est éteint, figé, sans courant ou sans Internet,
il ne peut rien dire. Le service extérieur, lui, remarque le silence. L'admin y crée une fois une
« vérification » et colle son adresse de signal ici ; Jabba l'appelle toutes les 5 minutes. L'adresse
contient un identifiant secret : gardée dans un fichier lisible par l'appli seule, jamais renvoyée à l'écran.

Sommaire :
  SIGNUP_PAGE     : la page où créer la vérification
  status()        : en place ?, dernier signal, et s'il est passé
  save(url)       : essaie l'adresse (un premier signal), puis la garde
  forget()        : l'oublie (le service extérieur préviendra alors du silence : à mettre en pause là-bas)
  watch(now)      : toutes les 5 minutes, un signal de vie
  interne : _ping
"""

import os
import re
import time

import httpx

import config
from services import json_files
from services.service_error import ServiceError

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
SETTINGS_FILE = os.path.join(DATA_DIR, "surveillance.json")
SIGNUP_PAGE = "https://healthchecks.io/"
URL = re.compile(r"https://[a-z0-9.-]+\.[a-z]{2,}(:\d{2,5})?/[A-Za-z0-9/_.~-]{4,200}")
EVERY_SECONDS = 5 * 60


def _ping(url):
    """Un signal de vie ; vrai si le service l'a reçu."""
    if config.DEMO:
        return True
    try:
        return httpx.get(url, timeout=10, follow_redirects=False).status_code < 300
    except httpx.HTTPError:
        return False


def status():
    settings = json_files.load(SETTINGS_FILE, {})
    return {
        "ready": bool(settings.get("url")),
        "last": settings.get("last"),
        "ok": settings.get("ok"),
        "page": SIGNUP_PAGE,
    }


def save(url):
    url = str(url or "").strip()
    if not URL.fullmatch(url):
        raise ServiceError(400, "Colle l'adresse de signal en entier (elle commence par https://hc-ping.com/…).")
    if not _ping(url):
        raise ServiceError(400, "Le service n'a pas reçu le signal : vérifie l'adresse, puis réessaie.")
    json_files.save(SETTINGS_FILE, {"url": url, "last": int(time.time()), "ok": True}, private=True)
    return status()


def forget():
    json_files.save(SETTINGS_FILE, {}, private=True)
    return status()


def watch(now=None):
    now = now or time.time()
    settings = json_files.load(SETTINGS_FILE, {})
    if not settings.get("url") or now - settings.get("attempt", 0) < EVERY_SECONDS - 30:
        return None
    ok = _ping(settings["url"])
    settings.update(attempt=int(now), ok=ok)
    if ok:
        settings["last"] = int(now)
    json_files.save(SETTINGS_FILE, settings, private=True)
    return ok
