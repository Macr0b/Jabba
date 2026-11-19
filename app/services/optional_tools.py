"""Outils à activer en option dans l'Établi (Vaultwarden, Immich, Time Machine…) et la mémoire.

Le catalogue et les fichiers d'installation sont fixés dans host/catalog.py : ici, on dit ce qui
est installé, ce que chaque outil prendrait en mémoire, et on lance l'installation (tâche longue).
Retirer un outil arrête ses conteneurs mais garde ses données.

Sommaire :
  BUILT_IN               : les outils qui font partie de l'appli (mémoire d'atelier), activés sans conteneur
  memory_budget(usage)   : mémoire du serveur (totale, utilisée, prise par les outils, libre)
  catalog(installed)     : les outils en option, installés ou non, avec la mémoire qu'ils prendraient
  install(name, ...)     : installation en tâche longue (mot de passe pour Time Machine)
  remove(name, who)      : retrait (données gardées)
"""

from host import actions
from host import catalog as host_catalog
from services import ad_blocker, background_tasks, measurements, workshop

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
            "mot_de_passe": tool["mot_de_passe"],
            "mot_de_passe_texte": tool.get("mot_de_passe_texte", ""),
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
            "mot_de_passe": False,
            "integre": True,
            "page": tool["page"],
        }
        for tool_id, tool in BUILT_IN.items()
    ]


def install(name, password=None, who=None):
    """Lève ActionError si l'outil ou le mot de passe est refusé ; sinon, la tâche longue (None pour un outil de l'appli)."""
    if name in BUILT_IN:
        BUILT_IN[name]["activer"](True)
        return None
    if name not in host_catalog.OPTIONAL_TOOLS:
        raise actions.ActionError(f"Outil refusé : {name!r}")
    actions.optional_password(name, password or "")
    if name == "adguard-home":
        ad_blocker.save_password(password)  # l'appli pilote AdGuard (appareils filtrés ou non)
    tool = host_catalog.OPTIONAL_TOOLS[name]
    return background_tasks.start_task(
        f"Installation de {tool['nom']}", "installer_outil_option", {"outil": name}, who, name, password or ""
    )


def remove(name, who=None):
    if name in BUILT_IN:
        BUILT_IN[name]["activer"](False)  # les données restent
        return None
    if name not in host_catalog.OPTIONAL_TOOLS:
        raise actions.ActionError(f"Outil refusé : {name!r}")
    tool = host_catalog.OPTIONAL_TOOLS[name]
    return background_tasks.start_task(f"Retrait de {tool['nom']}", "retirer_outil_option", {"outil": name}, who, name)
