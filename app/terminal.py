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

TICKETS = {}          # ticket -> {qui, nom, expire}
DUREE_TICKET = 30
OUVERTS = {}          # pid -> {qui, debut}


def nouveau_ticket(qui, nom):
    maintenant = time.time()
    for t in [t for t, v in TICKETS.items() if v["expire"] < maintenant]:
        TICKETS.pop(t, None)
    t = secrets.token_urlsafe(32)
    TICKETS[t] = {"qui": qui, "nom": nom, "expire": maintenant + DUREE_TICKET}
    return t


def prendre_ticket(t):
    v = TICKETS.pop(t or "", None)
    return v if v and v["expire"] > time.time() else None


def _taille(fd, lignes, colonnes):
    lignes, colonnes = max(5, min(int(lignes), 500)), max(20, min(int(colonnes), 500))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", lignes, colonnes, 0, 0))


def lancer(commande, demo=False):
    """Ouvre un shell dans un pseudo-terminal. Renvoie (processus, descripteur maître)."""
    maitre, esclave = pty.openpty()

    def prepare():
        os.setsid()
        fcntl.ioctl(esclave, termios.TIOCSCTTY, 0)

    env = {"TERM": "xterm-256color", "LANG": "C.UTF-8", "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    if demo:
        env["HOME"] = "/tmp"
    p = subprocess.Popen(commande, stdin=esclave, stdout=esclave, stderr=esclave, preexec_fn=prepare,
                         close_fds=True, env=env)
    os.close(esclave)
    os.set_blocking(maitre, False)
    return p, maitre


async def relier(ws, commande, demo=False):
    """Fait passer les octets entre la page (xterm.js) et le shell, jusqu'à ce que l'un des deux s'arrête."""
    p, fd = lancer(commande, demo)
    OUVERTS[p.pid] = {"debut": time.time()}
    boucle = asyncio.get_running_loop()
    sortie = asyncio.Queue()

    def lisible():
        try:
            donnees = os.read(fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            donnees = b""
        sortie.put_nowait(donnees)

    boucle.add_reader(fd, lisible)

    async def vers_page():
        while True:
            d = await sortie.get()
            if not d:
                return
            await ws.send_bytes(d)

    async def depuis_page():
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                return
            if m.get("bytes") is not None:
                os.write(fd, m["bytes"])
                continue
            try:
                msg = json.loads(m.get("text") or "{}")
            except ValueError:
                continue
            if msg.get("t") == "i":
                os.write(fd, str(msg.get("d", "")).encode())
            elif msg.get("t") == "r":
                _taille(fd, msg.get("l", 24), msg.get("c", 80))

    taches = [asyncio.create_task(vers_page()), asyncio.create_task(depuis_page())]
    try:
        await asyncio.wait(taches, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in taches:
            t.cancel()
        boucle.remove_reader(fd)
        try:
            os.killpg(p.pid, signal.SIGHUP)
        except OSError:
            pass
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
        os.close(fd)
        OUVERTS.pop(p.pid, None)
    return p.returncode
