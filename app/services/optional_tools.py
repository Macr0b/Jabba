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
  remove_leftover(name, who) : retrait complet d'un outil que Jabba ne connaît plus (ntfy…)
"""

from host import actions, actions_tools
from host import catalog as host_catalog
from services import background_tasks, measurements, tool_addresses, tool_credentials, tool_setup, workshop

GIGA = 1024**3
# Outils de l'appli elle-même : rien à installer sur le serveur, juste un écran de plus.
BUILT_IN = {
    "atelier": {
        "name": "Mémoire d'atelier",
        "role": "Le carnet de chaque machine : interventions, pannes, réglages, rappels d'entretien",
        "page": "#/atelier",
        "active": workshop.is_enabled,
        "enable": workshop.set_enabled,
    }
}


def memory_budget(usage):
    memory = measurements.memory()
    used_by_tools = sum(item.get("memory", 0) for item in usage.values())
    return {
        "total": memory["total"],
        "used": memory["used"],
        "tools": used_by_tools,
        "free": max(memory["total"] - memory["used"], 0),
    }


def catalog(installed_ids):
    return [
        {
            "id": tool_id,
            "name": tool["name"],
            "role": tool["role"],
            "memory": int(tool["memory_gb"] * GIGA),
            "installed": tool_id in installed_ids,
            "port_https": tool["port_https"],
        }
        for tool_id, tool in host_catalog.OPTIONAL_TOOLS.items()
    ] + [
        {
            "id": tool_id,
            "name": tool["name"],
            "role": tool["role"],
            "memory": 0,
            "installed": tool["active"](),
            "built_in": True,
            "page": tool["page"],
        }
        for tool_id, tool in BUILT_IN.items()
    ]


def install(name, who=None):
    """Lève ActionError si l'outil est refusé ; sinon, la tâche longue (None pour un outil de l'appli)."""
    if name in BUILT_IN:
        BUILT_IN[name]["enable"](True)
        return None
    if name not in host_catalog.OPTIONAL_TOOLS:
        raise actions.ActionError(f"Outil refusé : {name!r}")
    tool = host_catalog.OPTIONAL_TOOLS[name]
    first_time = tool_credentials.read(name) is None
    password = tool_credentials.password_for(name) if tool["password"] else ""
    actions_tools.optional_password(name, password)

    def then(output):
        # Réinstallation d'un outil installé à la main : ses réglages sont gardés, le mot de passe tiré ne sert pas.
        if "REGLAGES_GARDES" in output and first_time:
            tool_credentials.drop_unused_password(name)
        # Dans la foulée : son réglage (Nextcloud, Jellyfin), puis son adresse à part.
        steps = (tool_setup.after_install(name, who), tool_addresses.after_install(name, who))
        return "\n".join(line for line in steps if line)

    return background_tasks.start_task(
        f"Installation de {tool['name']}",
        "installer_outil_option",
        {"outil": name},
        who,
        name,
        password,
        then=then,
    )


def remove(name, who=None):
    if name in BUILT_IN:
        BUILT_IN[name]["enable"](False)  # les données restent
        return None
    if name not in host_catalog.OPTIONAL_TOOLS:
        raise actions.ActionError(f"Outil refusé : {name!r}")
    tool = host_catalog.OPTIONAL_TOOLS[name]
    return background_tasks.start_task(f"Retrait de {tool['name']}", "retirer_outil_option", {"outil": name}, who, name)


def remove_leftover(name, who=None):
    """Un outil d'une ancienne version (retiré du catalogue) : conteneurs, volumes, dossier, adresse."""
    actions_tools.leftover_tool({"outil": name})
    return background_tasks.start_task(f"Retrait de {name}", "retirer_ancien_outil", {"outil": name}, who, name)
