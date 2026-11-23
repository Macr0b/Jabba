"""Les listes de réglages système de Nextcloud (adresses acceptées, relais de confiance), par occ.

Sommaire :
  system_list(key)          : les valeurs d'une liste (une par ligne), vide si absente
  add_to_system_list(key, value) : ajoute la valeur si elle n'y est pas déjà
"""

from host import commands


def system_list(key):
    code, output, _ = commands.occ(["config:system:get", key])
    return [line.strip() for line in output.splitlines() if line.strip()] if code == 0 else []


def add_to_system_list(key, value):
    values = system_list(key)
    if value not in values:
        commands.occ(["config:system:set", key, str(len(values)), "--value=" + value])
