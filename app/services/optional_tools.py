"""Outils à activer en option dans l'Établi (Vaultwarden, Immich, Time Machine…) et la mémoire.

Le catalogue et les fichiers d'installation sont fixés dans host/catalog.py : ici, on dit ce qui
est installé, ce que chaque outil prendrait en mémoire, et on lance l'installation (tâche longue).
Retirer un outil arrête ses conteneurs mais garde ses données.

Sommaire :
  BUILT_IN               : les outils qui font partie de l'appli (mémoire d'atelier), activés sans conteneur
  memory_budget(usage)   : mémoire du serveur (totale, utilisée, prise par les outils, libre)
  catalog(installed)     : les outils en option, installés ou non, avec la mémoire qu'ils prendraient
  install(name, who)     : installation en tâche longue ; le compte de l'outil (AdGuard, Paperless,
                           Time Machine) reçoit un mot de passe tiré au sort, rien à taper
  remove(name, who)      : retrait (données gardées)
"""

from host import actions, actions_tools
from host import catalog as host_catalog
from services import background_tasks, measurements, tool_addresses, tool_credentials, workshop

GIGA = 1024**3
# Outils de l'appli elle-même : rien à installer sur le serveur, juste un écran de plus.
BUILT_IN = {
    "atelier": {
        "nom": "Mémoire d'atelier",
        "role": "Le carnet de chaque machine : interventions, pannes, réglages, rappels d'entretien",
        "page": "#/atelier",
        "actif": workshop.is_enabled,
        "activer": workshop.set_enabled,
    }
}


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
            "port_https": tool["port_https"],
        }
        for tool_id, tool in host_catalog.OPTIONAL_TOOLS.items()
    ] + [
        {
            "id": tool_id,
            "nom": tool["nom"],
            "role": tool["role"],
            "memoire": 0,
            "installe": tool["actif"](),
            "integre": True,
            "page": tool["page"],
        }
        for tool_id, tool in BUILT_IN.items()
    ]


def install(name, who=None):
    """Lève ActionError si l'outil est refusé ; sinon, la tâche longue (None pour un outil de l'appli)."""
    if name in BUILT_IN:
        BUILT_IN[name]["activer"](True)
        return None
    if name not in host_catalog.OPTIONAL_TOOLS:
        raise actions.ActionError(f"Outil refusé : {name!r}")
    tool = host_catalog.OPTIONAL_TOOLS[name]
    password = tool_credentials.password_for(name) if tool["mot_de_passe"] else ""
    actions_tools.optional_password(name, password)
    return background_tasks.start_task(
        f"Installation de {tool['nom']}",
        "installer_outil_option",
        {"outil": name},
        who,
        name,
        password,
        then=lambda: tool_addresses.after_install(name, who),  # son adresse à part, dans la foulée
    )


def remove(name, who=None):
    if name in BUILT_IN:
        BUILT_IN[name]["activer"](False)  # les données restent
        return None
    if name not in host_catalog.OPTIONAL_TOOLS:
        raise actions.ActionError(f"Outil refusé : {name!r}")
    tool = host_catalog.OPTIONAL_TOOLS[name]
    return background_tasks.start_task(f"Retrait de {tool['nom']}", "retirer_outil_option", {"outil": name}, who, name)
