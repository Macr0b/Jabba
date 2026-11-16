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
    _, sortie, _ = commands.run_on_host(["ip", "-o", "-4", "route", "get", "1.1.1.1"])
    m = re.search(r"dev (\S+) .*src (\S+)", sortie)
    return (m.group(1), m.group(2)) if m else (None, None)


def sync_guest_tools():
    """Copie, pour la page invités, la liste des outils et leur état (la porte du Wi-Fi ne parle pas à Docker)."""
    outils = []
    for o in tools.list_tools():
        if o["id"] in tools.PORTS and o["id"] not in tools.NEVER_ON_WIFI:
            outils.append({"id": o["id"], "nom": o["nom"], "role": o["role"], "port": tools.PORTS[o["id"]],
                           "actif": o["etat"] != "arrêté", "lien_tailscale": o.get("lien")})
    _, ip = wifi_address()

    def f(d):
        d["outils"], d["adresse"] = outils, ip
    guests.update(f)


FIREWALL_CHAIN = "JABBA-WIFI"


def wanted_rules(interface, d):
    """Une règle par outil fermé au Wi-Fi : bloque son port pour ce qui arrive par la carte réseau du Wi-Fi."""
    regles = []
    for pid, port in sorted(tools.PORTS.items()):
        if pid in tools.NEVER_ON_WIFI or not d["wifi"].get(pid):
            regles.append(f"-A {FIREWALL_CHAIN} -i {interface} -p tcp -m conntrack --ctorigdstport {port} --ctdir ORIGINAL -j DROP")
    return regles


def apply_firewall():
    """Les outils non cochés « Sur le Wi-Fi » ne sont plus joignables depuis le réseau local (Tailscale n'est pas touché)."""
    if config.DEMO:
        return True
    interface, _ = wifi_address()
    if not interface or not re.fullmatch(r"[\w.-]+", interface):
        return False
    voulues = wanted_rules(interface, guests.read())
    ok = True
    for outil_ipt in ("iptables", "ip6tables"):
        if commands.run_on_host([outil_ipt, "-S", "DOCKER-USER"])[0] != 0:
            continue
        code, actuelles, _ = commands.run_on_host([outil_ipt, "-S", FIREWALL_CHAIN])
        if code == 0 and [l for l in actuelles.splitlines() if l.startswith("-A ")] == voulues and \
                commands.run_on_host([outil_ipt, "-C", "DOCKER-USER", "-j", FIREWALL_CHAIN])[0] == 0:
            continue
        script = f"{outil_ipt} -N {FIREWALL_CHAIN} 2>/dev/null; {outil_ipt} -F {FIREWALL_CHAIN}"
        for r in voulues:
            script += f" && {outil_ipt} {r}"
        script += f" && ({outil_ipt} -C DOCKER-USER -j {FIREWALL_CHAIN} 2>/dev/null || {outil_ipt} -I DOCKER-USER -j {FIREWALL_CHAIN})"
        code, _, err = commands.run_shell_on_host(script)
        if code != 0:
            ok = False
            print("pare-feu :", outil_ipt, err[:200], flush=True)
    return ok


def guest_url(d):
    ip = d.get("adresse") or wifi_address()[1]
    return f"http://{ip}/?c={d['code']}" if ip else None


def shareable_roots():
    """Disques branchés et dossier Invités, en plus des dossiers du serveur."""
    r = {n: c for n, c in files_source.known_disks().items() if n in files_source.plugged_disks()}
    r["Invités"] = guests.GUEST_FOLDER
    return r
