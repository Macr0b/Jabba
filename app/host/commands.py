"""host.commands"""
import os
import re
import subprocess
import config


# ---------------------------------------------------------------- hôte
def run_on_host(cmd, timeout=30, entree=None):
    """Lance une commande sur le serveur (pas dans le conteneur)."""
    complet = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--"] + cmd
    r = subprocess.run(complet, capture_output=True, text=True, timeout=timeout, input=entree)
    return r.returncode, r.stdout, r.stderr


def run_on_host_with_input(cmd, entree, timeout=30):
    return run_on_host(cmd, timeout=timeout, entree=entree)


TASKS_DIR = "/var/lib/jabba/taches"


def task_command(unite, interprete="sh", proprietes=()):
    """La commande systemd-run qui lance le script déjà posé pour `unite` (voir lancer_sur_serveur)."""
    return (["systemd-run", f"--unit={unite}", "--collect"] + [x for p in proprietes for x in ("-p", p)]
            + [interprete, f"{TASKS_DIR}/{unite}.sh"])


def start_on_host(unite, script, interprete="sh", proprietes=()):
    """Lance `script` sur le serveur, en tâche de fond (systemd-run) : elle va au bout même si l'appli redémarre.

    Le script est d'abord écrit dans un fichier, puis lancé depuis ce fichier. Surtout pas en ligne
    de commande : systemd y remplace lui-même tout ce qui ressemble à ${…} avant de le passer au
    shell, ce qui casse le script en silence. Le fichier s'efface dès que le script démarre.
    """
    if not re.fullmatch(r"[a-z0-9-]{3,60}", unite):
        raise ValueError("Nom de tâche inattendu")
    chemin = f"{TASKS_DIR}/{unite}.sh"
    code, _, err = run_on_host_with_input(["sh", "-c", f"umask 077 && mkdir -p {TASKS_DIR} && cat > {chemin}"],
                               'rm -f "$0"\n' + script)
    if code != 0:
        return code, "", err
    return run_on_host(task_command(unite, interprete, proprietes))


def run_shell_on_host(script, timeout=30):
    return run_on_host(["sh", "-c", script], timeout)


# ---------------------------------------------------------------- comptes Nextcloud
DEMO_OCC = os.environ.get("JABBA_DEMO_OCC")   # démo : « php /chemin/vers/nextcloud/occ »


def occ(args, entree=None, mdp=False, timeout=60):
    """Une commande d'administration de Nextcloud. Avec `mdp`, `entree` est un mot de passe, passé par l'entrée standard."""
    if config.DEMO:
        if not DEMO_OCC:
            return 1, "", "Nextcloud absent (démo)."
        env = dict(os.environ)
        if mdp:
            env["OC_PASS"], entree = entree, None
        try:
            r = subprocess.run(DEMO_OCC.split() + args, capture_output=True, text=True, timeout=timeout, input=entree, env=env)
        except subprocess.TimeoutExpired:
            return 1, "", "Nextcloud ne répond pas."
        return r.returncode, r.stdout, r.stderr
    base = ["docker", "exec"] + (["-e", "OC_PASS"] if mdp else []) + ["-u", "www-data", "nextcloud", "php", "occ", "--no-warnings"]
    try:
        if mdp:
            return run_on_host(["sh", "-c", 'export OC_PASS="$(cat)"; exec "$@"', "sh"] + base + args, timeout, entree)
        return run_on_host(base + args, timeout)
    except subprocess.TimeoutExpired:
        return 1, "", "Nextcloud ne répond pas."
