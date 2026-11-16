"""Terminal du serveur, dans l'appli.

Réservé au super admin, mot de passe redemandé (s'il date de plus de 10 minutes).
Le shell tourne sur le serveur lui-même (hors du conteneur), avec le compte du
serveur : `sudo` y redemande le mot de passe, comme en SSH.

Déroulé : l'appli demande un ticket (requête HTTP ordinaire, donc contrôlée
comme les autres : Tailscale, session, rang, mot de passe récent), puis ouvre
la connexion WebSocket avec ce ticket, valable 30 secondes et une seule fois.
"""

import asyncio
import fcntl
import json
import os
import pty
import secrets
import signal
import struct
import subprocess
import termios
import time

TICKETS = {}  # ticket -> {qui, nom, expire}
TICKET_LIFETIME = 30
OPEN_TERMINALS = {}  # pid -> {qui, debut}


def new_ticket(who, name):
    now = time.time()
    for ticket in [ticket for ticket, ticket_data in TICKETS.items() if ticket_data["expire"] < now]:
        TICKETS.pop(ticket, None)
    ticket = secrets.token_urlsafe(32)
    TICKETS[ticket] = {"qui": who, "nom": name, "expire": now + TICKET_LIFETIME}
    return ticket


def take_ticket(ticket):
    ticket_data = TICKETS.pop(ticket or "", None)
    return ticket_data if ticket_data and ticket_data["expire"] > time.time() else None


def _resize(descriptor, lines, columns):
    lines, columns = max(5, min(int(lines), 500)), max(20, min(int(columns), 500))
    fcntl.ioctl(descriptor, termios.TIOCSWINSZ, struct.pack("HHHH", lines, columns, 0, 0))


RC = r"""# Terminal de l'appli Jabba : réglages du shell, puis chaque commande part au journal du serveur.
# La langue du serveur (le terminal démarre en anglais sinon)
[ -f /etc/default/locale ] && . /etc/default/locale && export LANG LANGUAGE LC_ALL 2>/dev/null
[ -f ~/.bashrc ] && . ~/.bashrc
cd ~
HISTCONTROL=
__jabba_dernier=
__jabba_noter() {
  local n=$HISTCMD
  if [ -n "$__jabba_dernier" ] && [ "$n" != "$__jabba_dernier" ]; then
    logger -t jabba-terminal -- "$JABBA_SESSION $JABBA_QUI : $(fc -ln -1 | sed 's/^[[:space:]]*//')"
  fi
  __jabba_dernier=$n
}
PROMPT_COMMAND="__jabba_noter${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
echo "Connecté au compte $(whoami) du serveur. Tes commandes sont notées au Journal de Jabba au nom de $JABBA_QUI."
"""
RC_PATH = "/usr/local/lib/jabba/terminal.bashrc"
DEMO_LOG = "/tmp/jabba-terminal-demo.log"


def prepare_rc(write_rc):
    """Écrit le fichier de démarrage du shell sur le serveur (ou en démo, à côté) et renvoie son chemin."""
    if write_rc is None:  # démo : pas de journal système, on note dans un fichier
        path = "/tmp/jabba-terminal.bashrc"
        with open(path, "w") as file:
            file.write(RC.replace("logger -t jabba-terminal -- ", f"echo >> {DEMO_LOG} "))
        return path
    write_rc(RC)
    return RC_PATH


def command_log(session_id, started_at, host_runner):
    """Les commandes tapées pendant la session `sid`, dans l'ordre (200 au plus)."""
    if host_runner is None:
        try:
            lines = open(DEMO_LOG).read().splitlines()
        except OSError:
            lines = []
    else:
        _, output, _ = host_runner(
            ["journalctl", "-t", "jabba-terminal", "--since", f"@{int(started_at) - 1}", "-o", "cat", "--no-pager"]
        )
        lines = output.splitlines()
    result = []
    for line in lines:
        if line.startswith(session_id + " "):
            command = line[len(session_id) + 1 :].split(" : ", 1)[-1].strip()
            if command:
                result.append(command[:300])
    return result[:200]


def start(command, demo=False, extra_env=None):
    """Ouvre un shell dans un pseudo-terminal. Renvoie (processus, descripteur maître)."""
    master_fd, slave_fd = pty.openpty()

    def prepare():
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

    env = {
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    if demo:
        env["HOME"] = "/tmp"
    env.update(extra_env or {})
    process = subprocess.Popen(
        command, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, preexec_fn=prepare, close_fds=True, env=env
    )
    os.close(slave_fd)
    os.set_blocking(master_fd, False)
    return process, master_fd


async def bridge(ws, command, demo=False, env=None):
    """Fait passer les octets entre la page (xterm.js) et le shell, jusqu'à ce que l'un des deux s'arrête."""
    process, descriptor = start(command, demo, env)
    OPEN_TERMINALS[process.pid] = {"debut": time.time()}
    loop_index = asyncio.get_running_loop()
    output = asyncio.Queue()

    def lisible():
        try:
            incoming = os.read(descriptor, 65536)
        except BlockingIOError:
            return
        except OSError:
            incoming = b""
        output.put_nowait(incoming)

    loop_index.add_reader(descriptor, lisible)

    async def vers_page():
        while True:
            data = await output.get()
            if not data:
                return
            await ws.send_bytes(data)

    async def depuis_page():
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                os.write(descriptor, message["bytes"])
                continue
            try:
                msg = json.loads(message.get("text") or "{}")
            except ValueError:
                continue
            if msg.get("t") == "i":
                os.write(descriptor, str(msg.get("d", "")).encode())
            elif msg.get("t") == "r":
                _resize(descriptor, msg.get("l", 24), msg.get("c", 80))

    tasks = [asyncio.create_task(vers_page()), asyncio.create_task(depuis_page())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for ticket in tasks:
            ticket.cancel()
        loop_index.remove_reader(descriptor)
        try:
            os.killpg(process.pid, signal.SIGHUP)
        except OSError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        os.close(descriptor)
        OPEN_TERMINALS.pop(process.pid, None)
    return process.returncode
