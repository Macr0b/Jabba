"""La liste fermée des actions que l'appli peut demander au serveur.

Chaque action a un nom et des paramètres vérifiés ; la commande est construite ICI, jamais
reçue de l'extérieur. Le service privilégié (host/host_service.py) n'exécute que ce qui
figure dans cette liste : une faille dans l'interface web ne peut pas faire lancer
n'importe quelle commande sur le serveur (recommandation ANSSI : moindre privilège).

Sommaire
- ActionError                 : paramètre refusé.
- build(name, args)           : (commande, délai) pour une action connue, sinon ActionError.
- check_input(name, text)     : seules certaines actions reçoivent un texte sur l'entrée standard (un mot de passe).
- host_input(name, args, text): ce qui part vraiment vers la commande (rien pour une vérification de mot de passe).
- finish(name, result, text)  : le résultat rendu à l'appli (une vérification ne rend que oui ou non).
- NAMES                       : les actions connues.

Toutes les commandes de l'appli sur le serveur passent par ici. Elles sont rangées par famille :
host/actions_disks.py (disques), host/actions_tools.py (outils, domaine, Nextcloud),
host/actions_system.py (système, réseau invités, sauvegardes, compte du serveur) ; ce qu'elles
partagent (vérifications, tâches de fond) est dans host/action_common.py.
"""

import hmac

from host import actions_disks, actions_system, actions_tools
from host.action_common import ActionError
from host.actions_tools import AUTH_KEY, optional_password, optional_tool

INPUT_ACTIONS = frozenset(
    {
        "nextcloud",
        "verifier_mot_de_passe_serveur",
        "changer_mot_de_passe_serveur",
        "installer_outil_option",
        "adresse_outil",
    }
)


def check_input(name, text):
    """Un texte pour l'entrée standard n'est accepté que par les actions prévues pour."""
    if text is None:
        return None
    if name not in INPUT_ACTIONS or not isinstance(text, str) or len(text) > 4096:
        raise ActionError(f"Entrée refusée pour {name!r}")
    return text


def host_input(name, args, text):
    """Le texte donné à la commande sur le serveur. Le mot de passe à vérifier, lui, ne quitte pas le service."""
    if name == "verifier_mot_de_passe_serveur":
        return None
    if name == "installer_outil_option":
        return optional_password(optional_tool(args)[0], text)
    if name == "adresse_outil":
        if not AUTH_KEY.fullmatch(text or ""):
            raise ActionError("Clé d'inscription Tailscale refusée.")
        return text
    if name == "changer_mot_de_passe_serveur":
        if not text or ":" in text or "\n" in text:
            raise ActionError("Mot de passe refusé (ni « : » ni retour à la ligne).")
        return f"{args['utilisateur']}:{text}\n"
    return text


def finish(name, result, text):
    """Pour une vérification de mot de passe, l'empreinte de /etc/shadow reste dans le service :
    l'appli reçoit seulement 0 (bon), 1 (mauvais) ou 2 (compte illisible ou verrouillé)."""
    if name != "verifier_mot_de_passe_serveur":
        return result
    from host import system_crypt

    code, output, error_output = result
    if code != 0:
        return code, "", error_output[:200]
    fields = output.split(":")
    password_hash = fields[1] if len(fields) > 1 else ""
    if not password_hash or password_hash[0] in "!*":
        return 2, "", "Compte verrouillé ou illisible."
    computed = system_crypt.hash_with_setting(text or "", password_hash) or ""
    if hmac.compare_digest(computed, password_hash):
        return 0, "ok", ""
    return 1, "", "Mot de passe incorrect."


ACTIONS = {**actions_disks.ACTIONS, **actions_tools.ACTIONS, **actions_system.ACTIONS}
NAMES = tuple(ACTIONS)


def build(name, args=None):
    builder = ACTIONS.get(name)
    if builder is None:
        raise ActionError(f"Action inconnue : {name!r}")
    if args is not None and not isinstance(args, dict):
        raise ActionError("Paramètres refusés")
    return builder(args or {})
