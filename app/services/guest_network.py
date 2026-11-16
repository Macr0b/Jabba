"""services.guest_network"""

import re
from services import guests
import config
from host import commands
from services import files_source
from services import tools


# ---------------------------------------------------------------- invités : réglages (admin)
def wifi_address():
    """Interface et adresse IP du serveur sur le réseau local (celles de la route par défaut)."""
    if config.DEMO:
        return "eth0", "192.168.1.20"
    _, output, _ = commands.run_on_host(["ip", "-o", "-4", "route", "get", "1.1.1.1"])
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
                    "nom": tool["nom"],
                    "role": tool["role"],
                    "port": tools.PORTS[tool["id"]],
                    "actif": tool["etat"] != "arrêté",
                    "lien_tailscale": tool.get("lien"),
                }
            )
    _, address = wifi_address()

    def f(state):
        state["outils"], state["adresse"] = tools_list, address

    guests.update(f)


FIREWALL_CHAIN = "JABBA-WIFI"


def wanted_rules(interface, state):
    """Une règle par outil fermé au Wi-Fi : bloque son port pour ce qui arrive par la carte réseau du Wi-Fi."""
    rules = []
    for pid, port in sorted(tools.PORTS.items()):
        if pid in tools.NEVER_ON_WIFI or not state["wifi"].get(pid):
            rules.append(
                f"-A {FIREWALL_CHAIN} -i {interface} -p tcp -m conntrack --ctorigdstport {port} --ctdir ORIGINAL -j DROP"
            )
    return rules


def apply_firewall():
    """Les outils non cochés « Sur le Wi-Fi » ne sont plus joignables depuis le réseau local (Tailscale n'est pas touché)."""
    if config.DEMO:
        return True
    interface, _ = wifi_address()
    if not interface or not re.fullmatch(r"[\w.-]+", interface):
        return False
    wanted = wanted_rules(interface, guests.read())
    changed = True
    for iptables_tool in ("iptables", "ip6tables"):
        if commands.run_on_host([iptables_tool, "-S", "DOCKER-USER"])[0] != 0:
            continue
        code, current_rules, _ = commands.run_on_host([iptables_tool, "-S", FIREWALL_CHAIN])
        if (
            code == 0
            and [line for line in current_rules.splitlines() if line.startswith("-A ")] == wanted
            and commands.run_on_host([iptables_tool, "-C", "DOCKER-USER", "-j", FIREWALL_CHAIN])[0] == 0
        ):
            continue
        script = f"{iptables_tool} -N {FIREWALL_CHAIN} 2>/dev/null; {iptables_tool} -F {FIREWALL_CHAIN}"
        for roots in wanted:
            script += f" && {iptables_tool} {roots}"
        script += f" && ({iptables_tool} -C DOCKER-USER -j {FIREWALL_CHAIN} 2>/dev/null || {iptables_tool} -I DOCKER-USER -j {FIREWALL_CHAIN})"
        code, _, error_output = commands.run_shell_on_host(script)
        if code != 0:
            changed = False
            print("pare-feu :", iptables_tool, error_output[:200], flush=True)
    return changed


def guest_url(state):
    address = state.get("adresse") or wifi_address()[1]
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
