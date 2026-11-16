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
def api_terminal_ticket(request: Request):
    """Réservé au compte du serveur, mot de passe récent. Le ticket ouvre une seule connexion, dans les 30 s.

    Le terminal donne tous les pouvoirs sur le serveur (sudo, Docker…) : il n'est ouvert qu'à
    celui qui les a déjà, le propriétaire du serveur. Le Journal des commandes est indicatif :
    il note ce qui est tapé dans le shell, mais un shell lancé à la main lui échappe.
    """
    j = guard.exiger_admin(request, "super", fort=True)
    if j["identifiant"] != guard.nom_hote():
        raise HTTPException(403, "Le terminal donne tous les pouvoirs sur le serveur : il est réservé au compte du serveur.")
    notifications.envoyer("securite", "Terminal ouvert", f"{j['identifiant']} ({request.state.nom}) a ouvert le terminal du serveur.",
                   "/#/journal", famille="admin")
    return {"ticket": terminal.nouveau_ticket(j["identifiant"], request.state.nom), "compte": guard.admin_user()}


@router.websocket("/api/terminal/ws")
async def ws_terminal(ws: WebSocket, ticket: str = ""):
    hote_page = (ws.headers.get("host") or "").split(":")[0]
    origine = (ws.headers.get("origin") or "").split("//")[-1].split(":")[0]
    qui = terminal.prendre_ticket(ticket)
    if not qui or not origine or origine != hote_page:
        await ws.close(code=4403)
        return
    await ws.accept()
    compte = guard.admin_user()
    sid = secrets.token_hex(4)
    # Chaque commande tapée part au journal du serveur, signée par le compte Jabba
    # (pas seulement par le compte Linux, partagé par tous les super admins).
    env = {"JABBA_QUI": qui["qui"], "JABBA_SESSION": sid}
    if config.DEMO:
        commande = ["bash", "--rcfile", terminal.preparer_rc(None), "-i"]
    else:
        rc = terminal.preparer_rc(lambda contenu: commands.hote_entree(
            ["sh", "-c", "mkdir -p /usr/local/lib/jabba && cat > /usr/local/lib/jabba/terminal.bashrc"], contenu))
        commande = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--",
                    "runuser", "-u", compte, "--", "bash", "--rcfile", rc, "-i"]
    debut = time.time()
    activity_log.noter("admin", f"Terminal ouvert par {qui['qui']} (compte {compte} du serveur)", qui["nom"])
    await terminal.relier(ws, commande, demo=config.DEMO, env=env)
    for cmd in terminal.commandes(sid, debut, None if config.DEMO else commands.hote):
        activity_log.noter("admin", f"Terminal · {qui['qui']} : {cmd}", qui["nom"])
    activity_log.noter("admin", f"Terminal fermé ({qui['qui']}, {max(1, round((time.time() - debut) / 60))} min)", qui["nom"])
