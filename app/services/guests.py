"""Accès invités : ce que voient les personnes connectées au Wi-Fi, sans compte.

Deux portes vers les mêmes pages invités :
- le Wi-Fi : un petit serveur séparé (port 80, http://<serveur>.local), qui
  ne connaît QUE les pages invités. La connexion admin y est impossible. Il tourne
  dans son propre conteneur, sans aucun pouvoir : compte du serveur (pas root),
  aucune capacité Linux, système de fichiers en lecture seule. Il ne voit que
  /srv (les dossiers partagés) et son petit dossier d'état (donnees/porte-wifi/),
  jamais le reste des données de l'appli (comptes, clés, sessions…).
- Tailscale : l'appli, tant qu'on n'est pas connecté en admin.

Sur le Wi-Fi, il faut le code à 4 chiffres (ou le QR code qui le contient).
L'appareil est ensuite reconnu 7 jours, sauf si l'admin change le code.
Les invités ne voient que les dossiers et les outils choisis par l'admin.

Sommaire :
  read                  : l'état de l'accès invités (actif, code, dossiers, outils ouverts au Wi-Fi)
  write                 : enregistre cet état, lisible par le compte des invités
  prepare_state         : au démarrage de l'appli : dossier d'état des invités (et reprise de l'ancien emplacement)
  update                : change l'état d'un coup, sans qu'un autre geste s'intercale
  new_code              : tire un nouveau code ; les invités déjà entrés doivent le retaper
  prepare_guest_folder  : crée le dossier Invités au compte du serveur
  guest_cookie          : le cookie signé d'un invité qui a donné le bon code
  cookie_is_valid       : vrai si le cookie est signé, à jour (code inchangé) et pas expiré
  record_failure        : un code faux de plus pour cette adresse
  is_blocked            : vrai après 8 mauvais codes en 10 minutes depuis cette adresse
  interne : _give_to_guest_account, _sign

Les pages des invités (routes) sont dans routes/guests.py.
"""

import hashlib
import hmac
import os
import secrets
import threading
import time

import config
from services import json_files

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
# État partagé entre l'appli (qui l'écrit) et la porte du Wi-Fi (qui le lit) : un dossier à part.
STATE_FILE = os.environ.get("JABBA_INVITES_ETAT", os.path.join(DATA_DIR, "porte-wifi", "etat.json"))
OLD_STATE_FILE = os.path.join(DATA_DIR, "invites.json")  # avant la 0.11
GUEST_FOLDER = os.environ.get("JABBA_DOSSIER_INVITES", "/srv/data/invites")
COOKIE = "jabba_invite"
# Dépôts des invités : taille maximale d'un fichier, et place qu'on laisse toujours libre sur le disque.
MAX_FILE_BYTES = int(float(os.environ.get("JABBA_INVITES_MAX_GO", "4")) * 1024**3)
KEEP_FREE_BYTES = int(float(os.environ.get("JABBA_INVITES_LIBRE_GO", "5")) * 1024**3)
COOKIE_LIFETIME = 7 * 86400
STATIC_DIR = config.STATIC_DIR
_lock = threading.RLock()
_failures = {}  # ip -> [horodatages]


# ---------------------------------------------------------------- réglages
def read():
    state = json_files.load(STATE_FILE)
    state.setdefault("actif", False)
    state.setdefault("code", "%04d" % secrets.randbelow(10000))
    state.setdefault("generation", 1)
    state.setdefault("secret", secrets.token_hex(32))
    state.setdefault("dossiers", [{"id": "invites", "nom": "Invités", "chemin": GUEST_FOLDER, "mode": "depot"}])
    state.setdefault("wifi", {"jellyfin": True})  # outils joignables depuis le Wi-Fi
    state.setdefault("outils", [])  # copie tenue à jour par l'appli : {id, nom, role, port, actif}
    state.setdefault("adresse", None)  # adresse IP du serveur sur le Wi-Fi
    return state


def write(state):
    with _lock:
        json_files.save(STATE_FILE, state, private=True, before_replace=_give_to_guest_account)


GUEST_UID = int(os.environ.get("JABBA_UID", "1000"))
GUEST_GID = int(os.environ.get("JABBA_GID", "1000"))


def _give_to_guest_account(path):
    """L'appli (root) écrit l'état ; la porte du Wi-Fi le lit avec le compte du serveur."""
    if os.geteuid() == 0:
        try:
            os.chown(path, GUEST_UID, GUEST_GID)
        except OSError:
            pass


def prepare_state():
    """Au démarrage de l'appli : dossier d'état des invités (et reprise de l'ancien emplacement)."""
    folder = os.path.dirname(STATE_FILE)
    os.makedirs(folder, exist_ok=True)
    if os.path.exists(OLD_STATE_FILE) and not os.path.exists(STATE_FILE):
        os.replace(OLD_STATE_FILE, STATE_FILE)
    if not os.path.exists(STATE_FILE):
        write(read())  # code et secret créés une fois pour toutes, ici
    if os.geteuid() == 0:
        os.chmod(folder, 0o700)
        _give_to_guest_account(folder)
        _give_to_guest_account(STATE_FILE)


def update(function):
    with _lock:
        state = read()
        current_state = function(state)
        write(state)
        return current_state


def new_code():
    def f(state):
        state["code"] = "%04d" % secrets.randbelow(10000)
        state["generation"] += 1  # les invités déjà reconnus doivent ressaisir le code
        return state["code"]

    return update(f)


def prepare_guest_folder():
    try:
        os.makedirs(GUEST_FOLDER, exist_ok=True)
        stat = os.stat(os.path.dirname(GUEST_FOLDER))
        os.chown(GUEST_FOLDER, 1000, stat.st_gid or 1000)
    except OSError:
        pass


# ---------------------------------------------------------------- cookie invité
def _sign(state, content):
    return hmac.new(bytes.fromhex(state["secret"]), content.encode(), hashlib.sha256).hexdigest()[:32]


def guest_cookie(state):
    expiry = int(time.time()) + COOKIE_LIFETIME
    content = f"{state['generation']}.{expiry}"
    return f"{content}.{_sign(state, content)}"


def cookie_is_valid(state, value):
    try:
        generation, expiry, signature = (value or "").split(".")
    except ValueError:
        return False
    return (
        hmac.compare_digest(signature, _sign(state, f"{generation}.{expiry}"))
        and int(generation) == state["generation"]
        and int(expiry) > time.time()
    )


def record_failure(address):
    """Un code faux depuis cette adresse : au bout de 8 en 10 minutes, elle est bloquée (is_blocked)."""
    _failures.setdefault(address, []).append(time.time())


def is_blocked(address):
    now = time.time()
    _failures[address] = [attempts for attempts in _failures.get(address, []) if attempts > now - 600]
    return len(_failures[address]) >= 8
