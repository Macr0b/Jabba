"""Le rangement des cartes (bouton « Ranger ») : l'ordre et les cartes masquées, par écran, sur le serveur.

Le même rangement sur tous les appareils. Une carte qui n'est pas dans l'ordre garde sa place de
départ, à la suite ; une carte masquée ne s'affiche plus que pendant le rangement (pour la remettre).

Sommaire :
  SCREENS             : les écrans qu'on peut ranger
  read(screen)        : {"order": [...], "hidden_items": [...]}
  save(screen, order, hidden) : enregistre ; refuse ce qui n'est pas une liste d'identifiants de cartes
  interne : _clean
"""

import os
import threading

from services import json_files

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
ORDER_FILE = os.path.join(DATA_DIR, "rangement.json")
OLD_FILE = os.path.join(DATA_DIR, "ordre-etabli.json")  # 0.16.3 en préparation : l'Établi seul
SCREENS = {"etabli", "accueil-mesures", "accueil-tuiles"}
MAX_CARDS = 200
MAX_ID = 300  # une tuile d'accès rapide a pour identifiant son chemin
_LOCK = threading.Lock()


def _clean(cards):
    if not isinstance(cards, list) or len(cards) > MAX_CARDS:
        raise ValueError("Rangement refusé.")
    clean = []
    for card in cards:
        if not isinstance(card, str) or not card or len(card) > MAX_ID or any(ord(c) < 32 for c in card):
            raise ValueError("Rangement refusé.")
        if card not in clean:
            clean.append(card)
    return clean


def read(screen):
    if screen not in SCREENS:
        raise ValueError("Écran inconnu.")
    saved = json_files.load(ORDER_FILE, {}).get(screen)
    if saved is None and screen == "etabli":
        saved = {"order": json_files.load(OLD_FILE, {}).get("order", []), "hidden_items": []}
    saved = saved or {}
    return {"order": saved.get("order", []), "hidden_items": saved.get("hidden_items", [])}


def save(screen, order, hidden=None):
    if screen not in SCREENS:
        raise ValueError("Écran inconnu.")
    entry = {"order": _clean(order), "hidden_items": _clean(hidden or [])}
    with _LOCK:
        saved = json_files.load(ORDER_FILE, {})
        saved[screen] = entry
        json_files.save(ORDER_FILE, saved)
    return entry
