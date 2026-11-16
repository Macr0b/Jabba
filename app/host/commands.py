"""host.commands"""
import os
import re
import subprocess
import config


# ---------------------------------------------------------------- hôte
def hote(cmd, timeout=30, entree=None):
    """Lance une commande sur le serveur (pas dans le conteneur)."""
    complet = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--"] + cmd
    r = subprocess.run(complet, capture_output=True, text=True, timeout=timeout, input=entree)
    return r.returncode, r.stdout, r.stderr


def hote_entree(cmd, entree, timeout=30):
    return hote(cmd, timeout=timeout, entree=entree)


DOSSIER_TACHES = "/var/lib/jabba/taches"


def commande_tache(unite, interprete="sh", proprietes=()):
    """La commande systemd-run qui lance le script déjà posé pour `unite` (voir lancer_sur_serveur)."""
    return (["systemd-run", f"--unit={unite}", "--collect"] + [x for p in proprietes for x in ("-p", p)]
            + [interprete, f"{DOSSIER_TACHES}/{unite}.sh"])


def lancer_sur_serveur(unite, script, interprete="sh", proprietes=()):
    """Lance `script` sur le serveur, en tâche de fond (systemd-run) : elle va au bout même si l'appli redémarre.

    Le script est d'abord écrit dans un fichier, puis lancé depuis ce fichier. Surtout pas en ligne
    de commande : systemd y remplace lui-même tout ce qui ressemble à ${…} avant de le passer au
    shell, ce qui casse le script en silence. Le fichier s'efface dès que le script démarre.
    """
    if not re.fullmatch(r"[a-z0-9-]{3,60}", unite):
        raise ValueError("Nom de tâche inattendu")
    chemin = f"{DOSSIER_TACHES}/{unite}.sh"
    code, _, err = hote_entree(["sh", "-c", f"umask 077 && mkdir -p {DOSSIER_TACHES} && cat > {chemin}"],
                               'rm -f "$0"\n' + script)
    if code != 0:
        return code, "", err
    return hote(commande_tache(unite, interprete, proprietes))


def hote_sh(script, timeout=30):
    return hote(["sh", "-c", script], timeout)


# ---------------------------------------------------------------- comptes Nextcloud
OCC_DEMO = os.environ.get("JABBA_DEMO_OCC")   # démo : « php /chemin/vers/nextcloud/occ »


def occ(args, entree=None, mdp=False, timeout=60):
    """Une commande d'administration de Nextcloud. Avec `mdp`, `entree` est un mot de passe, passé par l'entrée standard."""
    if config.DEMO:
        if not OCC_DEMO:
            return 1, "", "Nextcloud absent (démo)."
        env = dict(os.environ)
        if mdp:
            env["OC_PASS"], entree = entree, None
        try:
            r = subprocess.run(OCC_DEMO.split() + args, capture_output=True, text=True, timeout=timeout, input=entree, env=env)
        except subprocess.TimeoutExpired:
            return 1, "", "Nextcloud ne répond pas."
        return r.returncode, r.stdout, r.stderr
    base = ["docker", "exec"] + (["-e", "OC_PASS"] if mdp else []) + ["-u", "www-data", "nextcloud", "php", "occ", "--no-warnings"]
    try:
        if mdp:
            return hote(["sh", "-c", 'export OC_PASS="$(cat)"; exec "$@"', "sh"] + base + args, timeout, entree)
        return hote(base + args, timeout)
    except subprocess.TimeoutExpired:
        return 1, "", "Nextcloud ne répond pas."
