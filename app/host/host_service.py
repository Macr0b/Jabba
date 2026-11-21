"""Le service privilégié : le seul à agir sur le serveur, et seulement par la liste fermée (host/actions.py).

Il tourne dans son propre conteneur (jabba-hote, voir compose.yaml), sans réseau, et n'écoute
que sur une prise locale (socket Unix) partagée avec l'appli.

Protocole : une ligne JSON {"action": "...", "args": {...}} ; réponse : une ligne JSON
{"code": 0, "sortie": "...", "erreur": "..."}. Toute action inconnue ou tout paramètre
refusé répond code 126, sans rien lancer.

Sommaire
- SOCKET_PATH, MAX_REQUEST_BYTES
- run(command, timeout) : lance la commande dans les espaces de noms du serveur (nsenter).
- handle(raw)           : traite une demande (testable sans socket).
- main()                : écoute.

Lancement : python -m host.host_service
  interne : _client
"""

import asyncio
import json
import os
import subprocess
import sys

from host import actions

SOCKET_PATH = os.environ.get("JABBA_HOTE_SOCKET", "/run/jabba/hote.sock")
MAX_REQUEST_BYTES = 64 * 1024
REFUSED = 126


def run(command, timeout, input_text=None):
    full_command = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--", *command]
    try:
        completed = subprocess.run(full_command, capture_output=True, text=True, timeout=timeout, input=input_text)
    except subprocess.TimeoutExpired:
        return 124, "", "Délai dépassé"
    return completed.returncode, completed.stdout, completed.stderr


def handle(raw, runner=None):
    runner = runner or run
    try:
        request = json.loads(raw)
        name = request["action"]
        command, timeout = actions.build(name, request.get("args"))
        input_text = actions.check_input(name, request.get("entree"))
        to_host = actions.host_input(name, request.get("args"), input_text)
    except (ValueError, KeyError, TypeError) as error:
        return {"code": REFUSED, "sortie": "", "erreur": f"Refusé : {error}"}
    print("action:", name, flush=True)
    result = runner(command, timeout, to_host)
    code, output, error_output = actions.finish(name, result, input_text)
    return {"code": code, "sortie": output, "erreur": error_output}


async def _client(reader, writer):
    try:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=10)
        except ValueError:  # ligne plus longue que la limite
            raw = b""
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            answer = {"code": REFUSED, "sortie": "", "erreur": "Demande vide ou trop longue"}
        else:
            answer = await asyncio.to_thread(handle, raw.decode("utf-8", "replace"))
        writer.write((json.dumps(answer) + "\n").encode())
        await writer.drain()
    except (asyncio.TimeoutError, ConnectionError):
        pass
    finally:
        writer.close()


async def main():
    os.makedirs(os.path.dirname(SOCKET_PATH), exist_ok=True)
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    server = await asyncio.start_unix_server(_client, path=SOCKET_PATH, limit=MAX_REQUEST_BYTES + 1)
    os.chmod(SOCKET_PATH, 0o660)  # l'appli seule (même groupe) peut s'y connecter
    print("service privilégié prêt :", SOCKET_PATH, "—", len(actions.NAMES), "actions", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
