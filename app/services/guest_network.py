"""services.guest_network"""
import re
from services import guests
import config
from host import commands
from services import files_source
from services import tools


# ---------------------------------------------------------------- invités : réglages (admin)
def adresse_wifi():
    """Interface et adresse IP du serveur sur le réseau local (celles de la route par défaut)."""
    if config.DEMO:
        return "eth0", "192.168.1.20"
    _, sortie, _ = commands.hote(["ip", "-o", "-4", "route", "get", "1.1.1.1"])
    m = re.search(r"dev (\S+) .*src (\S+)", sortie)
    return (m.group(1), m.group(2)) if m else (None, None)


def synchroniser_outils_invites():
    """Copie, pour la page invités, la liste des outils et leur état (la porte du Wi-Fi ne parle pas à Docker)."""
    outils = []
    for o in tools.lister_outils():
        if o["id"] in tools.PORTS and o["id"] not in tools.JAMAIS_WIFI:
            outils.append({"id": o["id"], "nom": o["nom"], "role": o["role"], "port": tools.PORTS[o["id"]],
                           "actif": o["etat"] != "arrêté", "lien_tailscale": o.get("lien")})
    _, ip = adresse_wifi()

    def f(d):
        d["outils"], d["adresse"] = outils, ip
    guests.modifier(f)


CHAINE = "JABBA-WIFI"


def regles_voulues(interface, d):
    """Une règle par outil fermé au Wi-Fi : bloque son port pour ce qui arrive par la carte réseau du Wi-Fi."""
    regles = []
    for pid, port in sorted(tools.PORTS.items()):
        if pid in tools.JAMAIS_WIFI or not d["wifi"].get(pid):
            regles.append(f"-A {CHAINE} -i {interface} -p tcp -m conntrack --ctorigdstport {port} --ctdir ORIGINAL -j DROP")
    return regles


def appliquer_pare_feu():
    """Les outils non cochés « Sur le Wi-Fi » ne sont plus joignables depuis le réseau local (Tailscale n'est pas touché)."""
    if config.DEMO:
        return True
    interface, _ = adresse_wifi()
    if not interface or not re.fullmatch(r"[\w.-]+", interface):
        return False
    voulues = regles_voulues(interface, guests.lire())
    ok = True
    for outil_ipt in ("iptables", "ip6tables"):
        if commands.hote([outil_ipt, "-S", "DOCKER-USER"])[0] != 0:
            continue
        code, actuelles, _ = commands.hote([outil_ipt, "-S", CHAINE])
        if code == 0 and [l for l in actuelles.splitlines() if l.startswith("-A ")] == voulues and \
                commands.hote([outil_ipt, "-C", "DOCKER-USER", "-j", CHAINE])[0] == 0:
            continue
        script = f"{outil_ipt} -N {CHAINE} 2>/dev/null; {outil_ipt} -F {CHAINE}"
        for r in voulues:
            script += f" && {outil_ipt} {r}"
        script += f" && ({outil_ipt} -C DOCKER-USER -j {CHAINE} 2>/dev/null || {outil_ipt} -I DOCKER-USER -j {CHAINE})"
        code, _, err = commands.hote_sh(script)
        if code != 0:
            ok = False
            print("pare-feu :", outil_ipt, err[:200], flush=True)
    return ok


def url_invites(d):
    ip = d.get("adresse") or adresse_wifi()[1]
    return f"http://{ip}/?c={d['code']}" if ip else None


def racines_partageables():
    """Disques branchés et dossier Invités, en plus des dossiers du serveur."""
    r = {n: c for n, c in files_source.disques_connus().items() if n in files_source.disques_branches()}
    r["Invités"] = guests.DOSSIER_INVITES
    return r
