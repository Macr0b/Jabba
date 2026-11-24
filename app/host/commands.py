"""Actions sur le serveur lui-même, demandées au service privilégié (conteneur jabba-hote).

L'appli n'a aucun droit sur le serveur : elle demande une action de la liste fermée
(host/actions.py) au service, par sa prise locale. Tout passe par ce fichier.

Sommaire :
  run_action : une action de la liste fermée, vérifiée ici puis par le service -> (code, sortie, erreur)
  occ : une commande d'administration de Nextcloud (action « nextcloud »)
  interne : _ask_service
"""

import json
import os
import socket
import subprocess

import config
from host import actions

HOST_SOCKET = os.environ.get("JABBA_HOTE_SOCKET", "/run/jabba/hote.sock")
NO_SERVICE = "Le service du serveur (jabba-hote) ne répond pas."


# ---------------------------------------------------------------- liste fermée
def run_action(action_name, /, input_text=None, **args):
    """Demande une action de la liste fermée. Un paramètre refusé lève ActionError tout de suite,
    avant même d'écrire au service (qui vérifie de nouveau de son côté)."""
    _, timeout = actions.build(action_name, args)
    actions.host_input(action_name, args, actions.check_input(action_name, input_text))
    reply = _ask_service({"action": action_name, "args": args, "entry": input_text}, timeout)
    # « sortie » et « erreur » : réponse d'un service resté en 0.16 pendant la mise à jour.
    output = reply.get("output", reply.get("sortie", ""))
    return reply.get("code", 1), output, reply.get("error", reply.get("erreur", ""))


def _ask_service(request, timeout):
    """Une demande au service par sa prise ; sa réponse (dict), ou un échec s'il ne répond pas."""
    answer = b""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout + 10)
            connection.connect(HOST_SOCKET)
            connection.sendall((json.dumps(request) + "\n").encode())
            while not answer.endswith(b"\n"):
                chunk = connection.recv(65536)
                if not chunk:
                    break
                answer += chunk
        return json.loads(answer)
    except (OSError, ValueError):
        return {"code": 1, "output": "", "error": NO_SERVICE}


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
            command=args[0],
            arguments=list(args[1:]),
            delay=int(timeout),
            password=bool(is_password),
        )
    except actions.ActionError as error:
        return 1, "", str(error)
