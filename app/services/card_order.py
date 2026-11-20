"""L'ordre des cartes de l'Établi, choisi par l'admin (bouton « Ranger »), le même sur tous les appareils.

Sommaire :
  read()        : la liste des identifiants d'outils, dans l'ordre voulu (vide = ordre de départ)
  save(order)   : l'enregistre ; refuse ce qui n'est pas une liste d'identifiants d'outils
"""

import os
import re

from services import json_files

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
ORDER_FILE = os.path.join(DATA_DIR, "ordre-etabli.json")
TOOL_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
MAX_CARDS = 100


def read():
    return json_files.load(ORDER_FILE, {}).get("ordre", [])


def save(order):
    if not isinstance(order, list) or len(order) > MAX_CARDS:
        raise ValueError("Ordre refusé.")
    clean = []
    for tool in order:
        if not isinstance(tool, str) or not TOOL_ID.fullmatch(tool):
            raise ValueError("Ordre refusé.")
        if tool not in clean:
            clean.append(tool)
    json_files.save(ORDER_FILE, {"ordre": clean})
    return clean
