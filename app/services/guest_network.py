"""Réseau des invités : adresse Wi-Fi du serveur, outils ouverts au Wi-Fi, pare-feu.

Sommaire :
  wifi_address : adresse du serveur sur le Wi-Fi
  sync_guest_tools : copie la liste des outils pour la porte du Wi-Fi
  closed_ports, apply_firewall : bloquer au Wi-Fi les outils qui n'y sont pas ouverts
  guest_url, shareable_roots : adresse de la page invités, dossiers partageables
"""

import re

import config
from host import actions, commands
from services import files_source, guests, tools


# ---------------------------------------------------------------- invités : réglages (admin)
def wifi_address():
    """Interface et adresse IP du serveur sur le réseau local (celles de la route par défaut)."""
    if config.DEMO:
        return "eth0", "192.168.1.20"
    _, output, _ = commands.run_action("route_reseau")
    match = re.search(r"dev (\S+) .*src (\S+)", output)
    return (match.group(1), match.group(2)) if match else (None, None)


def sync_guest_tools():
    """Copie, pour la page invités, la liste des outils et leur état (la porte du Wi-Fi ne parle pas à Docker)."""
    tools_list = []
    for tool in tools.list_tools():
        if tool["id"] in tools.PORTS and tool["id"] not in tools.NEVER_ON_WIFI:
            tools_list.append(
                {
                    "id": tool["id"],
                    "name": tool["name"],
                    "role": tool["role"],
                    "port": tools.PORTS[tool["id"]],
                    "active": tool["state"] != "arrêté",
                    "tailscale_link": tool.get("link"),
                }
            )
    _, address = wifi_address()

    def f(state):
        state["tools"], state["address"] = tools_list, address

    guests.update(f)


def closed_ports(state):
    """Les ports des outils fermés au Wi-Fi (jamais ouverts, ou non cochés « Sur le Wi-Fi »)."""
    return sorted(port for pid, port in tools.PORTS.items() if pid in tools.NEVER_ON_WIFI or not state["wifi"].get(pid))


def apply_firewall():
    """Les outils non cochés « Sur le Wi-Fi » ne sont plus joignables depuis le réseau local (Tailscale n'est pas touché).
    Le service privilégié écrit lui-même les règles (action « pare_feu_wifi ») à partir des ports."""
    if config.DEMO:
        return True
    interface, _ = wifi_address()
    if not interface:
        return False
    try:
        code, _, error_output = commands.run_action(
            "pare_feu_wifi", interface=interface, ports=closed_ports(guests.read())
        )
    except actions.ActionError as error:
        print("pare-feu :", error, flush=True)
        return False
    if code != 0:
        print("pare-feu :", error_output[:200], flush=True)
    return code == 0


def guest_url(state):
    address = state.get("address") or wifi_address()[1]
    return f"http://{address}/?c={state['code']}" if address else None


def shareable_roots():
    """Disques branchés et dossier Invités, en plus des dossiers du serveur."""
    roots = {
        name: disk_path
        for name, disk_path in files_source.known_disks().items()
        if name in files_source.plugged_disks()
    }
    roots["Invités"] = guests.GUEST_FOLDER
    return roots
