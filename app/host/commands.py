"""host.commands"""

import os
import re
import subprocess
import config


# ---------------------------------------------------------------- hôte
def run_on_host(command, timeout=30, input_text=None):
    """Lance une commande sur le serveur (pas dans le conteneur)."""
    full_command = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--"] + command
    completed = subprocess.run(full_command, capture_output=True, text=True, timeout=timeout, input=input_text)
    return completed.returncode, completed.stdout, completed.stderr


def run_on_host_with_input(command, input_text, timeout=30):
    return run_on_host(command, timeout=timeout, input_text=input_text)


TASKS_DIR = "/var/lib/jabba/taches"


def task_command(unit, interpreter="sh", properties=()):
    """La commande systemd-run qui lance le script déjà posé pour `unite` (voir lancer_sur_serveur)."""
    return (
        ["systemd-run", f"--unit={unit}", "--collect"]
        + [argument for prop in properties for argument in ("-p", prop)]
        + [interpreter, f"{TASKS_DIR}/{unit}.sh"]
    )


def start_on_host(unit, script, interpreter="sh", properties=()):
    """Lance `script` sur le serveur, en tâche de fond (systemd-run) : elle va au bout même si l'appli redémarre.

    Le script est d'abord écrit dans un fichier, puis lancé depuis ce fichier. Surtout pas en ligne
    de commande : systemd y remplace lui-même tout ce qui ressemble à ${…} avant de le passer au
    shell, ce qui casse le script en silence. Le fichier s'efface dès que le script démarre.
    """
    if not re.fullmatch(r"[a-z0-9-]{3,60}", unit):
        raise ValueError("Nom de tâche inattendu")
    path = f"{TASKS_DIR}/{unit}.sh"
    code, _, error_output = run_on_host_with_input(
        ["sh", "-c", f"umask 077 && mkdir -p {TASKS_DIR} && cat > {path}"], 'rm -f "$0"\n' + script
    )
    if code != 0:
        return code, "", error_output
    return run_on_host(task_command(unit, interpreter, properties))


def run_shell_on_host(script, timeout=30):
    return run_on_host(["sh", "-c", script], timeout)


# ---------------------------------------------------------------- comptes Nextcloud
DEMO_OCC = os.environ.get("JABBA_DEMO_OCC")  # démo : « php /chemin/vers/nextcloud/occ »


def occ(args, input_text=None, is_password=False, timeout=60):
    """Une commande d'administration de Nextcloud. Avec `is_password`, `input_text` est un mot de passe, passé par l'entrée standard."""
    if config.DEMO:
        if not DEMO_OCC:
            return 1, "", "Nextcloud absent (démo)."
        env = dict(os.environ)
        if is_password:
            env["OC_PASS"], input_text = input_text, None
        try:
            completed = subprocess.run(
                DEMO_OCC.split() + args, capture_output=True, text=True, timeout=timeout, input=input_text, env=env
            )
        except subprocess.TimeoutExpired:
            return 1, "", "Nextcloud ne répond pas."
        return completed.returncode, completed.stdout, completed.stderr
    base = (
        ["docker", "exec"]
        + (["-e", "OC_PASS"] if is_password else [])
        + ["-u", "www-data", "nextcloud", "php", "occ", "--no-warnings"]
    )
    try:
        if is_password:
            return run_on_host(
                ["sh", "-c", 'export OC_PASS="$(cat)"; exec "$@"', "sh"] + base + args, timeout, input_text
            )
        return run_on_host(base + args, timeout)
    except subprocess.TimeoutExpired:
        return 1, "", "Nextcloud ne répond pas."
