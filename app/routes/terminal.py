"""routes.terminal"""
import time
import secrets
from services import terminal
from services import activity_log
from services import notifications
from fastapi import HTTPException, Request, WebSocket
import config
from host import commands
from security import guard
from fastapi import APIRouter

router = APIRouter()

# ---------------------------------------------------------------- terminal du serveur
@router.post("/api/terminal/ticket")
def terminal_ticket(request: Request):
    """Réservé au compte du serveur, mot de passe récent. Le ticket ouvre une seule connexion, dans les 30 s.

    Le terminal donne tous les pouvoirs sur le serveur (sudo, Docker…) : il n'est ouvert qu'à
    celui qui les a déjà, le propriétaire du serveur. Le Journal des commandes est indicatif :
    il note ce qui est tapé dans le shell, mais un shell lancé à la main lui échappe.
    """
    session = guard.require_admin(request, "super", strong=True)
    if session["identifiant"] != guard.hostname():
        raise HTTPException(403, "Le terminal donne tous les pouvoirs sur le serveur : il est réservé au compte du serveur.")
    notifications.send("securite", "Terminal ouvert", f"{session['identifiant']} ({request.state.nom}) a ouvert le terminal du serveur.",
                   "/#/journal", family="admin")
    return {"ticket": terminal.new_ticket(session["identifiant"], request.state.nom), "compte": guard.server_user()}


@router.websocket("/api/terminal/ws")
async def terminal_socket(ws: WebSocket, ticket: str = ""):
    page_host = (ws.headers.get("host") or "").split(":")[0]
    origin = (ws.headers.get("origin") or "").split("//")[-1].split(":")[0]
    who = terminal.take_ticket(ticket)
    if not who or not origin or origin != page_host:
        await ws.close(code=4403)
        return
    await ws.accept()
    account = guard.server_user()
    session_id = secrets.token_hex(4)
    # Chaque commande tapée part au journal du serveur, signée par le compte Jabba
    # (pas seulement par le compte Linux, partagé par tous les super admins).
    env = {"JABBA_QUI": who["qui"], "JABBA_SESSION": session_id}
    if config.DEMO:
        commande = ["bash", "--rcfile", terminal.prepare_rc(None), "-i"]
    else:
        rc_path = terminal.prepare_rc(lambda content: commands.run_on_host_with_input(
            ["sh", "-c", "mkdir -p /usr/local/lib/jabba && cat > /usr/local/lib/jabba/terminal.bashrc"], content))
        commande = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--",
                    "runuser", "-u", account, "--", "bash", "--rcfile", rc_path, "-i"]
    start = time.time()
    activity_log.record("admin", f"Terminal ouvert par {who['qui']} (compte {account} du serveur)", who["nom"])
    await terminal.bridge(ws, commande, demo=config.DEMO, env=env)
    for command in terminal.command_log(session_id, start, None if config.DEMO else commands.run_on_host):
        activity_log.record("admin", f"Terminal · {who['qui']} : {command}", who["nom"])
    activity_log.record("admin", f"Terminal fermé ({who['qui']}, {max(1, round((time.time() - start) / 60))} min)", who["nom"])
