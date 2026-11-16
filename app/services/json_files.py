"""Fichiers d'état en JSON : lecture tolérante, écriture sans jamais laisser un fichier à moitié écrit.

Sommaire
- load(path, default)                      : le contenu, ou une copie de `default` si absent ou illisible.
- save(path, data, private, before_replace): écrit dans un fichier temporaire, puis le met en place d'un coup.

Utilisé par chaque module qui garde un état (réglages, comptes, alertes…), pour ne pas répéter
ces quelques lignes partout. Les verrous restent dans chaque module : lui seul sait quoi protéger.
"""

import copy
import json
import os


def load(path, default=None):
    try:
        with open(path) as file:
            data = json.loads(file.read() or "null")
    except (OSError, ValueError):
        data = None
    if data is None:
        return copy.deepcopy(default) if default is not None else {}
    return data


def save(path, data, private=False, before_replace=None):
    """`private` : lisible par l'appli seule (0600). `before_replace(chemin_temporaire)` : dernier réglage."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temporary_file = path + ".tmp"
    with open(temporary_file, "w") as file:
        json.dump(data, file, ensure_ascii=False)
    if private:
        os.chmod(temporary_file, 0o600)
    if before_replace:
        before_replace(temporary_file)
    os.replace(temporary_file, path)
