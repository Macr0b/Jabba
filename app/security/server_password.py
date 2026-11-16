"""Mot de passe du super admin principal = mot de passe du compte du serveur (celui de sudo).

- vérifié contre /etc/shadow du serveur, jamais enregistré ;
- une fois saisi, valable 10 minutes (jeton en mémoire) ;
- 5 essais ratés = blocage 10 minutes ;
- changement du mot de passe, et code de secours en cas d'oubli.
"""

import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from host import system_crypt

TOKEN_LIFETIME = 10 * 60
MAX_ATTEMPTS = 5
LOCKOUT = 10 * 60
STATE_FILE = os.environ.get("JABBA_ADMIN", "/data/admin.json")

# 256 mots simples, sans accents : un code de 6 mots = 48 bits.
WORDS = """abeille abricot acier agneau aigle algue amande ananas ancre anneau arbre argent armoire astre atelier avion
bague baleine balle bambou banane barque bateau baton bergere biscuit bison bobine bois bonbon bouchon bougie
boussole bouton branche brique brosse bureau cactus cadre cafe caillou camion canard canoe carotte carte castor
cerise chaise chameau champ chapeau chariot chat chene cheval chevre chien citron clef cloche clou coffre
colline comete compas coquille corde cornet coton coussin crabe crayon criquet cuivre cygne dauphin desert diamant
domino dragon dune ecureuil enclume epice equerre erable escargot etoile faucon fenetre feuille ficelle figue flamme
fleur flute foret fourmi fraise frene fusee galet gant gateau gazelle girafe glacier gland gomme gourde
grain grenier griffe grotte guitare hamac hamster hibou horloge hublot igloo iguane jardin jasmin jonque kayak
koala lac lampe lapin laurier lavande lezard licorne lime lion livre loup loutre lune lynx marron
manche mangue marteau melon menthe meteore miel miroir moulin mouton muguet nuage oasis ocean olive orage
orange orchidee ours outil paille palmier panda papillon parasol pastel pecheur pelican perle phare piano pigeon
pinceau pirate planche plume poire pomme pont potager poulpe prairie puits radeau raisin renard requin rocher
roseau rubis sable sapin saule scie serpent singe soleil source sourire tabouret tambour tapis taureau theiere
tigre tipi tomate tortue toupie train trefle tresor tulipe vague valise vanille velo verger violon voile
volcan wagon yaourt zebre zinc abeillon atome aurore baobab bocal cabane caravane comptoir cristal bambin berceau
bonnet boule cadeau calme canne carafe cartable cerf cigale citrouille clairon coquelicot corbeau cormoran crocus dahlia
diadem echarpe eclair epee fanion fenouil flocon fougere framboise fromage gaufre geyser hirondelle houx jacinthe jonquille
kiwi lagune lanterne lilas lotus luciole mandarine marmotte mesange mimosa mouette myrtille narcisse noisette nougat oursin
pamplemousse panier papaye pelote perroquet pinson plateau poivron pompon prune quartz raquette renne rossignol safran sardine
sorbet tartine tilleul toucan vigne""".split()
assert len(set(WORDS)) >= 256

_lock = threading.Lock()
_tokens = {}  # jeton -> expiration
_failures = []  # horodatages des essais ratés


def _read():
    try:
        with open(STATE_FILE) as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}


def _write(data):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    temporary_file = STATE_FILE + ".tmp"
    with open(temporary_file, "w") as file:
        json.dump(data, file)
    os.chmod(temporary_file, 0o600)
    os.replace(temporary_file, STATE_FILE)


def is_locked():
    with _lock:
        now = time.time()
        _failures[:] = [timestamp for timestamp in _failures if timestamp > now - LOCKOUT]
        return len(_failures) >= MAX_ATTEMPTS


def _record_failure():
    with _lock:
        _failures.append(time.time())


def check_password(host_runner, user, password):
    """True si le mot de passe est celui du compte `utilisateur` sur le serveur."""
    if not password or is_locked():
        return False
    code, output, _ = host_runner(["getent", "shadow", user])
    if code != 0 or ":" not in output:
        return False
    password_hash = output.split(":")[1]
    if not password_hash or password_hash[0] in "!*":
        return False
    valid = hmac.compare_digest(system_crypt.hash_with_setting(password, password_hash) or "", password_hash)
    if not valid:
        _record_failure()
    return valid


def new_token(identifier="serveur", rank="super", temporary=False):
    """Jeton valable 10 minutes, qui dit qui est connecté en admin et avec quel rang."""
    jeton = secrets.token_urlsafe(32)
    with _lock:
        now = time.time()
        for token in [token for token, token_data in _tokens.items() if token_data["exp"] < now]:
            del _tokens[token]
        _tokens[jeton] = {"exp": now + TOKEN_LIFETIME, "identifiant": identifier, "rang": rank, "provisoire": temporary}
    return jeton


def token_info(token):
    """Les infos du jeton (identifiant, rang, provisoire) s'il est valable, sinon None."""
    if not token:
        return None
    with _lock:
        token_data = _tokens.get(token)
        return dict(token_data) if token_data and token_data["exp"] > time.time() else None


def forget_tokens(identifier=None):
    """Déconnecte un compte (ou tout le monde)."""
    with _lock:
        for token in [
            token
            for token, token_data in _tokens.items()
            if identifier is None or token_data["identifiant"] == identifier
        ]:
            del _tokens[token]


def record_failure():
    _record_failure()


def change_password(host_input_runner, user, new_value):
    if len(new_value) < 10:
        raise ValueError("Le nouveau mot de passe doit faire au moins 10 caractères.")
    if any(stored in new_value for stored in "\n\r:"):
        raise ValueError("Le mot de passe ne peut pas contenir « : » ni de retour à la ligne.")
    code, _, error_output = host_input_runner(["chpasswd"], f"{user}:{new_value}\n")
    if code != 0:
        raise RuntimeError("Le serveur a refusé le changement : " + error_output[:200])
    forget_tokens()  # on redemande le mot de passe partout


# ---------------------------------------------------------------- code de secours
def _code_hash(code, salt):
    normalized = " ".join(code.lower().split())
    return hashlib.scrypt(normalized.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()


def generate_recovery_code():
    code = " ".join(secrets.choice(WORDS) for _ in range(6))
    salt = secrets.token_hex(16)
    data = _read()
    data["code"] = {"sel": salt, "empreinte": _code_hash(code, salt), "cree": int(time.time())}
    _write(data)
    return code


def recovery_code_exists():
    return "code" in _read()


def check_recovery_code(code):
    if is_locked():
        return False
    stored = _read().get("code")
    valid = bool(stored) and hmac.compare_digest(_code_hash(code, stored["sel"]), stored["empreinte"])
    if not valid:
        _record_failure()
    return valid
