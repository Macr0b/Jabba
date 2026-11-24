"""Mot de passe du super admin principal = mot de passe du compte du serveur (celui de sudo).

- vérifié par le service privilégié contre /etc/shadow du serveur, jamais enregistré ;
- une fois saisi, valable 10 minutes (jeton en mémoire) ;
- 5 essais ratés = blocage 10 minutes ;
- changement du mot de passe, et code de secours en cas d'oubli.

Sommaire :
  is_locked               : true si la personne qui demande s'est trompée 5 fois en 10 minutes
  check_password          : true si le mot de passe est celui du compte `user` sur le serveur
  record_failure          : compte un essai raté (trop d'essais bloquent un moment)
  change_password         : change le mot de passe du serveur (règles vérifiées)
  generate_recovery_code  : tire un code de secours de 6 mots ; seule son empreinte est gardée
  recovery_code_exists    : vrai si un code de secours a été créé
  check_recovery_code     : vrai si le code de secours est le bon (sinon, compte un essai raté)
  interne : _read, _write, _record_failure, _code_hash
"""

import contextvars
import hashlib
import hmac
import os
import secrets
import threading
import time

from host import actions, commands
from security import password_rules
from services import json_files

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
# Essais ratés, comptés par personne (compte Tailscale d'où vient la demande) : quelqu'un qui
# se trompe, ou qui essaie de deviner, ne bloque que lui-même, jamais les autres admins.
_failures = {}  # compte Tailscale -> horodatages des essais ratés
requester = contextvars.ContextVar("requester", default="inconnu")


def _read():
    return json_files.load(STATE_FILE)


def _write(data):
    json_files.save(STATE_FILE, data, private=True)


def is_locked():
    """True si la personne qui demande s'est trompée 5 fois en 10 minutes."""
    who = requester.get()
    with _lock:
        now = time.time()
        recent = [timestamp for timestamp in _failures.get(who, []) if timestamp > now - LOCKOUT]
        if recent:
            _failures[who] = recent
        else:
            _failures.pop(who, None)
        return len(recent) >= MAX_ATTEMPTS


def _record_failure():
    with _lock:
        _failures.setdefault(requester.get(), []).append(time.time())


def check_password(user, password):
    """True si le mot de passe est celui du compte `user` sur le serveur.
    La comparaison se fait dans le service privilégié : l'empreinte de /etc/shadow n'en sort pas."""
    if not password or is_locked():
        return False
    try:
        code, _, _ = commands.run_action("verifier_mot_de_passe_serveur", input_text=password, user=user)
    except actions.ActionError:
        return False
    if code == 1:
        _record_failure()
    return code == 0


def record_failure():
    _record_failure()


def change_password(user, new_value):
    password_rules.check(new_value, (user,))
    if ":" in new_value or "\n" in new_value:
        raise ValueError("Le mot de passe du serveur ne peut pas contenir « : » ni de retour à la ligne.")
    try:
        code, _, error_output = commands.run_action("changer_mot_de_passe_serveur", input_text=new_value, user=user)
    except actions.ActionError as error:
        raise ValueError(str(error))
    if code != 0:
        raise RuntimeError("Le serveur a refusé le changement : " + error_output[:200])


# ---------------------------------------------------------------- code de secours
def _code_hash(code, salt):
    normalized = " ".join(code.lower().split())
    return hashlib.scrypt(normalized.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()


def generate_recovery_code():
    code = " ".join(secrets.choice(WORDS) for _ in range(6))
    salt = secrets.token_hex(16)
    data = _read()
    data["code"] = {"salt": salt, "fingerprint": _code_hash(code, salt), "created": int(time.time())}
    _write(data)
    return code


def recovery_code_exists():
    return "code" in _read()


def check_recovery_code(code):
    if is_locked():
        return False
    stored = _read().get("code")
    valid = bool(stored) and hmac.compare_digest(_code_hash(code, stored["salt"]), stored["fingerprint"])
    if not valid:
        _record_failure()
    return valid
