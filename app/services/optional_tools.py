"""Outils à activer en option dans l'Établi (Vaultwarden, Immich, Time Machine…) et la mémoire.

Le catalogue et les fichiers d'installation sont fixés dans host/catalog.py : ici, on dit ce qui
est installé, ce que chaque outil prendrait en mémoire, et on lance l'installation (tâche longue).
Retirer un outil arrête ses conteneurs mais garde ses données.

Sommaire :
  memory_budget(usage)   : mémoire du serveur (totale, utilisée, prise par les outils, libre)
  catalog(installed)     : les outils en option, installés ou non, avec la mémoire qu'ils prendraient
  install(name, ...)     : installation en tâche longue (mot de passe pour Time Machine)
  remove(name, who)      : retrait (données gardées)
"""

from host import actions
from host import catalog as host_catalog
from services import background_tasks, measurements

GIGA = 1024**3


def memory_budget(usage):
    memory = measurements.memory()
    used_by_tools = sum(item.get("memoire", 0) for item in usage.values())
    return {
        "total": memory["total"],
        "utilise": memory["utilise"],
        "outils": used_by_tools,
        "libre": max(memory["total"] - memory["utilise"], 0),
    }


def catalog(installed_ids):
    return [
        {
            "id": tool_id,
            "nom": tool["nom"],
            "role": tool["role"],
            "memoire": int(tool["memoire_go"] * GIGA),
            "installe": tool_id in installed_ids,
            "mot_de_passe": tool["mot_de_passe"],
            "port_https": tool["port_https"],
        }
        for tool_id, tool in host_catalog.OPTIONAL_TOOLS.items()
    ]


def install(name, password=None, who=None):
    """Lève ActionError si l'outil ou le mot de passe est refusé ; sinon, la tâche longue."""
    if name not in host_catalog.OPTIONAL_TOOLS:
        raise actions.ActionError(f"Outil refusé : {name!r}")
    actions.optional_password(name, password or "")
    tool = host_catalog.OPTIONAL_TOOLS[name]
    return background_tasks.start_task(
        f"Installation de {tool['nom']}", "installer_outil_option", {"outil": name}, who, name, password or ""
    )


def remove(name, who=None):
    if name not in host_catalog.OPTIONAL_TOOLS:
        raise actions.ActionError(f"Outil refusé : {name!r}")
    tool = host_catalog.OPTIONAL_TOOLS[name]
    return background_tasks.start_task(f"Retrait de {tool['nom']}", "retirer_outil_option", {"outil": name}, who, name)
