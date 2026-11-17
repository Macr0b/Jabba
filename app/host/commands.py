"""Commandes lancées sur le serveur lui-même (hors du conteneur de l'appli).

L'appli tourne dans un conteneur ; pour agir sur le serveur (disques, Docker, redémarrage…),
elle entre dans ses espaces de noms avec nsenter. Tout passe par ce fichier.

Sommaire :
  run_action : une action de la liste fermée (host/actions.py), par le service privilégié
  run_on_host : l'ancienne voie (nsenter depuis l'appli), utilisée seulement par run_action
                quand le service privilégié n'est pas là ; elle disparaîtra avec la dernière étape
  occ : une commande d'administration de Nextcloud (action « nextcloud »)
"""

import json
import os
import socket
import subprocess

import config
from host import actions

HOST_SOCKET = os.environ.get("JABBA_HOTE_SOCKET", "/run/jabba/hote.sock")


# ---------------------------------------------------------------- liste fermée
def run_action(name, input_text=None, **args):
    """Demande une action de la liste fermée. (code, sortie, erreur), comme run_on_host.

    Avec le service privilégié (sa prise existe) : la demande lui est envoyée.
    Sans lui (démo, tests, ancienne installation) : la même commande, construite par
    host/actions.py, est lancée par l'ancienne voie. Un paramètre refusé lève ActionError
    dans les deux cas.
    """
    command, timeout = actions.build(name, args)
    input_text = actions.check_input(name, input_text)
    to_host = actions.host_input(name, args, input_text)
    if not os.path.exists(HOST_SOCKET):
        return actions.finish(name, run_on_host(command, timeout=timeout, input_text=to_host), input_text)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout + 10)
            connection.connect(HOST_SOCKET)
            request = {"action": name, "args": args, "entree": input_text}
            connection.sendall((json.dumps(request) + "\n").encode())
            answer = b""
            while not answer.endswith(b"\n"):
                chunk = connection.recv(65536)
                if not chunk:
                    break
                answer += chunk
    except OSError:
        # Service arrêté (prise restée là) : tant que l'appli garde ses droits, l'ancienne voie dépanne.
        # Cette solution de secours disparaîtra avec la dernière étape (l'appli sans droits).
        return actions.finish(name, run_on_host(command, timeout=timeout, input_text=to_host), input_text)
    try:
        reply = json.loads(answer)
    except ValueError:
        return 1, "", "Le service du serveur ne répond pas."
    return reply.get("code", 1), reply.get("sortie", ""), reply.get("erreur", "")


# ---------------------------------------------------------------- hôte
def run_on_host(command, timeout=30, input_text=None):
    """Lance une commande sur le serveur (pas dans le conteneur)."""
    full_command = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--"] + command
    completed = subprocess.run(full_command, capture_output=True, text=True, timeout=timeout, input=input_text)
    return completed.returncode, completed.stdout, completed.stderr


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
    if not args:
        return 1, "", "Commande Nextcloud vide."
    try:
        return run_action(
            "nextcloud",
            input_text=input_text if is_password else None,
            commande=args[0],
            arguments=list(args[1:]),
            delai=int(timeout),
            mot_de_passe=bool(is_password),
        )
    except actions.ActionError as error:
        return 1, "", str(error)
    except subprocess.TimeoutExpired:
        return 1, "", "Nextcloud ne répond pas."
