"""Les listes de réglages système de Nextcloud (adresses acceptées, relais de confiance), par occ.

Et le filet de sécurité des ordinateurs synchronisés : une suppression faite sur un ordinateur se
répercute partout, mais le fichier reste dans la corbeille de Nextcloud au moins 30 jours (60 au plus).
Un réglage choisi à la main dans Nextcloud (autre que celui d'origine, « auto ») est laissé tel quel.

Sommaire :
  system_list(key)          : les valeurs d'une liste (une par ligne), vide si absente
  add_to_system_list(key, value) : ajoute la valeur si elle n'y est pas déjà
  TRASH_RETENTION           : la durée de la corbeille (au moins 30 jours, au plus 60)
  keep_deleted_files()      : règle la corbeille si elle est encore au réglage d'origine ; vrai si changé
"""

from host import commands

TRASH_RETENTION = "30, 60"


def system_list(key):
    code, output, _ = commands.occ(["config:system:get", key])
    return [line.strip() for line in output.splitlines() if line.strip()] if code == 0 else []


def add_to_system_list(key, value):
    values = system_list(key)
    if value not in values:
        commands.occ(["config:system:set", key, str(len(values)), "--value=" + value])


def keep_deleted_files():
    """Corbeille : au moins 30 jours, quoi qu'il arrive (le réglage d'origine peut la vider plus tôt)."""
    code, output, _ = commands.occ(["config:system:get", "trashbin_retention_obligation"])
    current = output.strip() if code == 0 else ""
    if current not in ("", "auto"):  # absent = réglage d'origine ; une autre valeur = choix de l'admin
        return False
    if code != 0 and output.strip():
        return False
    result, _, _ = commands.occ(["config:system:set", "trashbin_retention_obligation", "--value=" + TRASH_RETENTION])
    return result == 0
