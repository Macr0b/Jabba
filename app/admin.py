"""Mot de passe du super admin principal = mot de passe du compte du serveur (celui de sudo).

- vérifié contre /etc/shadow du serveur, jamais enregistré ;
- une fois saisi, valable 10 minutes (jeton en mémoire) ;
- 5 essais ratés = blocage 10 minutes ;
- changement du mot de passe, et code de secours en cas d'oubli.
"""
try:
    import crypt  # présent en 3.12 (image du serveur) ; gère yescrypt via libxcrypt
except ImportError:  # tests hors serveur
    crypt = None
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

DUREE_JETON = 10 * 60
ESSAIS_MAX = 5
BLOCAGE = 10 * 60
FICHIER = os.environ.get("JABBA_ADMIN", "/data/admin.json")

# 256 mots simples, sans accents : un code de 6 mots = 48 bits.
MOTS = """abeille abricot acier agneau aigle algue amande ananas ancre anneau arbre argent armoire astre atelier avion
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
assert len(set(MOTS)) >= 256

_verrou = threading.Lock()
_jetons = {}          # jeton -> expiration
_echecs = []          # horodatages des essais ratés


def _lire():
    try:
        with open(FICHIER) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _ecrire(d):
    os.makedirs(os.path.dirname(FICHIER), exist_ok=True)
    tmp = FICHIER + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, FICHIER)


def bloque():
    with _verrou:
        maintenant = time.time()
        _echecs[:] = [t for t in _echecs if t > maintenant - BLOCAGE]
        return len(_echecs) >= ESSAIS_MAX


def _echec():
    with _verrou:
        _echecs.append(time.time())


def verifier_mot_de_passe(hote, utilisateur, mot_de_passe):
    """True si le mot de passe est celui du compte `utilisateur` sur le serveur."""
    if not mot_de_passe or bloque():
        return False
    code, sortie, _ = hote(["getent", "shadow", utilisateur])
    if code != 0 or ":" not in sortie:
        return False
    empreinte = sortie.split(":")[1]
    if not empreinte or empreinte[0] in "!*":
        return False
    ok = hmac.compare_digest(crypt.crypt(mot_de_passe, empreinte) or "", empreinte)
    if not ok:
        _echec()
    return ok


def nouveau_jeton(identifiant="serveur", rang="super", provisoire=False):
    """Jeton valable 10 minutes, qui dit qui est connecté en admin et avec quel rang."""
    jeton = secrets.token_urlsafe(32)
    with _verrou:
        maintenant = time.time()
        for j in [j for j, v in _jetons.items() if v["exp"] < maintenant]:
            del _jetons[j]
        _jetons[jeton] = {"exp": maintenant + DUREE_JETON, "identifiant": identifiant, "rang": rang, "provisoire": provisoire}
    return jeton


def jeton_valide(jeton):
    """Les infos du jeton (identifiant, rang, provisoire) s'il est valable, sinon None."""
    if not jeton:
        return None
    with _verrou:
        v = _jetons.get(jeton)
        return dict(v) if v and v["exp"] > time.time() else None


def oublier_jetons(identifiant=None):
    """Déconnecte un compte (ou tout le monde)."""
    with _verrou:
        for j in [j for j, v in _jetons.items() if identifiant is None or v["identifiant"] == identifiant]:
            del _jetons[j]


def echec():
    _echec()


def changer_mot_de_passe(hote_entree, utilisateur, nouveau):
    if len(nouveau) < 10:
        raise ValueError("Le nouveau mot de passe doit faire au moins 10 caractères.")
    if any(c in nouveau for c in "\n\r:"):
        raise ValueError("Le mot de passe ne peut pas contenir « : » ni de retour à la ligne.")
    code, _, err = hote_entree(["chpasswd"], f"{utilisateur}:{nouveau}\n")
    if code != 0:
        raise RuntimeError("Le serveur a refusé le changement : " + err[:200])
    oublier_jetons()  # on redemande le mot de passe partout


# ---------------------------------------------------------------- code de secours
def _empreinte_code(code, sel):
    norm = " ".join(code.lower().split())
    return hashlib.scrypt(norm.encode(), salt=bytes.fromhex(sel), n=2 ** 14, r=8, p=1).hex()


def generer_code():
    code = " ".join(secrets.choice(MOTS) for _ in range(6))
    sel = secrets.token_hex(16)
    d = _lire()
    d["code"] = {"sel": sel, "empreinte": _empreinte_code(code, sel), "cree": int(time.time())}
    _ecrire(d)
    return code


def code_existe():
    return "code" in _lire()


def verifier_code(code):
    if bloque():
        return False
    c = _lire().get("code")
    ok = bool(c) and hmac.compare_digest(_empreinte_code(code, c["sel"]), c["empreinte"])
    if not ok:
        _echec()
    return ok
