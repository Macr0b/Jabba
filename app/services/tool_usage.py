"""Consommation de chaque outil : mémoire et processeur, d'après « docker stats ».

Sommaire
- parse_size(text)          : « 512MiB » → 536870912 octets.
- parse_stats(output)       : la sortie de docker stats → {conteneur: {cpu, memory}}.
- usage_by_tool(tools, stats): la somme par outil (un outil = plusieurs conteneurs).
- read_usage()              : tout ça, pour l'Établi.

Le processeur est en pourcentage d'un cœur (comme dans « top ») : 200 % = deux cœurs pleins.
"""

import json
import re

import config
from host import commands
from services import tools

UNITS = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4}
DEMO_USAGE = {"nextcloud": (2.4, 610 * 1024**2), "jellyfin": (0.8, 320 * 1024**2), "dockge": (0.1, 48 * 1024**2)}


def parse_size(text):
    match = re.fullmatch(r"\s*([\d.]+)\s*([a-zA-Z]+)\s*", text or "")
    if not match:
        return 0
    return int(float(match.group(1)) * UNITS.get(match.group(2).lower(), 0))


def parse_stats(output):
    found = {}
    for line in (output or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        name = row.get("Name") or row.get("Container")
        if not name:
            continue
        cpu = float(str(row.get("CPUPerc", "0")).rstrip("%") or 0)
        memory = parse_size(str(row.get("MemUsage", "")).split("/")[0])
        found[name] = {"cpu": cpu, "memory": memory}
    return found


def usage_by_tool(tool_list, stats):
    result = {}
    for tool in tool_list:
        names = [container["name"] for container in tool["containers"]]
        measured = [stats[name] for name in names if name in stats]
        if not measured:
            continue
        result[tool["id"]] = {
            "cpu": round(sum(item["cpu"] for item in measured), 1),
            "memory": sum(item["memory"] for item in measured),
        }
    return result


def read_usage():
    if config.DEMO:
        return {pid: {"cpu": cpu, "memory": memory} for pid, (cpu, memory) in DEMO_USAGE.items()}
    code, output, _ = commands.run_action("conso_conteneurs")
    if code != 0:
        return {}
    return usage_by_tool(tools.list_tools(), parse_stats(output))
