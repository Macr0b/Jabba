"""Mise à jour de l'appli depuis son dépôt GitHub public.

Le serveur regarde la dernière version publiée (une « release » vX.Y.Z).
Rien ne s'installe tout seul : l'admin clique, le serveur télécharge le code
de cette version, reconstruit l'appli et la relance. .env et donnees/ ne sont
jamais touchés (ils ne sont pas dans le dépôt).

Signature : chaque version publiée a, sur la branche main du dépôt, un fichier
signatures/<tag>.sig : sa première ligne est le texte signé, « jabba <tag> <commit> »,
suivie de la signature SSH faite avec une clé qui ne quitte pas l'ordinateur du mainteneur. Avant d'installer quoi que ce soit, le
serveur vérifie cette signature avec la clé publique de la version DÉJÀ installée
(cle_publication.pub, figée dans l'appli qui tourne) : un compte GitHub piraté ne
suffit pas à faire installer du code. Sans signature valable, rien ne s'installe.
"""

import os
import re
import time

import httpx

import config

RE_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
KEY_FILE = os.path.join(config.APP_DIR, "cle_publication.pub")
RE_KEY = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) [A-Za-z0-9+/=]{40,800}( [\w@.+ -]{0,100})?$")
SIGNATURE_NAMESPACE = "jabba"  # « namespace » de ssh-keygen -Y : une signature faite pour autre chose ne vaut rien ici
REFUSAL_MARK = "JABBA-SIGNATURE-REFUSEE"


def publication_key():
    """La clé publique qui signe les versions (celle de la version installée), ou None."""
    try:
        with open(KEY_FILE) as file:
            lines = [line.strip() for line in file if line.strip() and not line.startswith("#")]
    except OSError:
        return None
    return lines[0] if lines and RE_KEY.match(lines[0]) else None


def signed_text(tag, commit):
    return f"jabba {tag} {commit}"


_CACHE = {"t": 0, "v": None}


def version_tuple(version):
    match = RE_VERSION.match(version or "")
    return tuple(int(done_steps) for done_steps in match.groups()) if match else None


def latest_release(repository, force=False):
    """{'version': '0.5.1', 'tag': 'v0.5.1', 'notes': '...', 'date': '...'} ou None."""
    if not repository or not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        return None
    if not force and time.time() - _CACHE["t"] < 6 * 3600 and _CACHE["v"] is not None:
        return _CACHE["v"]
    try:
        response = httpx.get(
            f"https://api.github.com/repos/{repository}/releases/latest",
            timeout=15,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "jabba-app"},
        )
    except httpx.HTTPError:
        return _CACHE["v"]
    if response.status_code != 200:
        return _CACHE["v"]
    data = response.json()
    tag = data.get("tag_name", "")
    if not version_tuple(tag):
        return None
    version = {
        "version": tag.lstrip("v"),
        "tag": tag,
        "notes": (data.get("body") or "")[:3000],
        "date": data.get("published_at"),
    }
    _CACHE.update(t=time.time(), v=version)
    return version


def is_newer(current, other):
    current_tuple, other_tuple = version_tuple(current), version_tuple(other)
    return bool(current_tuple and other_tuple and other_tuple > current_tuple)


# Depuis la 0.15, l'appli n'a plus accès à tous les appareils : la carte graphique lui est donnée
# par compose.yaml (JABBA_GPU), posée ici pour les installations d'avant (même ligne qu'installer.sh).
GPU_SETTING = "[ -e /dev/dri/renderD128 ] && ! grep -q '^JABBA_GPU=' .env && echo 'JABBA_GPU=/dev/dri/renderD128' >> .env"


def install_script(folder, tag, key):
    """Commande lancée sur le serveur, hors du conteneur (elle survit au redémarrage de l'appli).

    Étapes : récupérer le dépôt, vérifier la signature de cette version avec `cle`
    (la clé de la version installée), puis installer exactement le commit signé.
    """
    if not re.fullmatch(r"/[\w./-]+", folder) or not RE_VERSION.match(tag):
        raise ValueError("Paramètres inattendus")
    if not key or not RE_KEY.match(key):
        raise ValueError("Pas de clé de publication : mise à jour impossible")
    # git tourne avec le compte propriétaire du dossier : les fichiers restent à son nom.
    # Avant, on lui rend tout ce qui ne lui appartient plus (traces d'anciennes installations
    # faites en root), sauf donnees/ qui reste à l'appli.
    owner = f"$(stat -c %U:%G '{folder}')"
    restore_owner = (
        f"find '{folder}' -path '{folder}/donnees' -prune -o ! -user \"$(stat -c %U '{folder}')\" "
        f'-exec chown -h "{owner}" {{}} +'
    )
    git_command = f"runuser -u \"$(stat -c %U '{folder}')\" -- git"
    # Le fichier de signature de cette version, sur main : « jabba <tag> <commit> » puis la signature.
    # Toute étape qui échoue (fichier absent, ligne inattendue, signature fausse) = refus.
    verify = (
        "T=$(mktemp -d) && trap 'rm -rf \"$T\"' EXIT && "
        f"printf '%s %s\\n' {SIGNATURE_NAMESPACE} '{key}' > \"$T/cles\" && "
        f"if {git_command} show 'refs/remotes/origin/main:signatures/{tag}.sig' > \"$T/fichier\" 2>/dev/null"
        ' && LIGNE=$(head -n 1 "$T/fichier")'
        f' && [ "${{LIGNE% *}}" = "jabba {tag}" ] && SHA="${{LIGNE##* }}"'
        ' && printf %s "$SHA" | grep -Eqx "[0-9a-f]{40}"'
        ' && sed -n \'/^-----BEGIN SSH SIGNATURE-----$/,/^-----END SSH SIGNATURE-----$/p\' "$T/fichier" > "$T/sig"'
        f' && printf %s "$LIGNE" | ssh-keygen -Y verify -f "$T/cles" -I {SIGNATURE_NAMESPACE} -n {SIGNATURE_NAMESPACE} -s "$T/sig"'
        f' && {git_command} cat-file -e "$SHA^{{commit}}"; then echo "Signature valable : {tag} ($SHA)"; '
        f"else echo '{REFUSAL_MARK} : la version {tag} n est pas signée par la clé attendue, rien n a été installé.'; exit 1; fi"
    )
    return (
        f"cd '{folder}' && {restore_owner} && {git_command} fetch --quiet --tags --force origin && {verify} && "
        f'{git_command} -c advice.detachedHead=false checkout --quiet --force "$SHA" && '
        f"{{ {GPU_SETTING} || true; }} && "
        "docker compose up -d --build && docker image prune -f"
    )


# ---- ajouté au découpage de main.py
import os
import re

import config
from host import commands
from services import notifications


# ---------------------------------------------------------------- mise à jour de l'appli
def app_folder():
    code, output, _ = commands.run_action("dossier_appli")
    data = output.strip()
    return data if code == 0 and re.fullmatch(r"/[\w./-]+", data) else None


CURRENT_UPDATE = {"unite": None, "version": None, "debut": 0, "signale": False}


def update_state():
    """Où en est la dernière installation lancée : en_cours, echec (avec le détail) ou termine."""
    if not CURRENT_UPDATE["unite"]:
        return {"statut": "aucune"}
    unit_name = CURRENT_UPDATE["unite"] + ".service"
    _, active, _ = commands.run_action("etat_tache", unite=unit_name)
    _, log, _ = commands.run_action("journal_tache", unite=unit_name)
    lines = [line for line in log.splitlines() if line.strip()]
    if active.strip() in ("active", "activating", "reloading"):
        return {
            "statut": "en_cours",
            "version": CURRENT_UPDATE["version"],
            "depuis": int(time.time() - CURRENT_UPDATE["debut"]),
            **update_progress(lines),
        }
    if any(REFUSAL_MARK in line for line in lines):
        if not CURRENT_UPDATE["signale"]:
            CURRENT_UPDATE["signale"] = True
            notifications.send(
                "securite",
                "Mise à jour refusée",
                f"La version {CURRENT_UPDATE['version']} publiée sur GitHub n'est pas signée par la clé attendue : rien n'a été installé. "
                "Si tu n'as rien publié toi-même, ton compte GitHub est peut-être piraté.",
                "/#/systeme",
                family="appli",
            )
        return {
            "statut": "echec",
            "version": CURRENT_UPDATE["version"],
            "detail": [
                "Signature refusée : cette version n'est pas signée par la clé attendue. Rien n'a été installé, l'appli actuelle continue."
            ],
        }
    if any("Failed with result" in line or "status=" in line and "status=0/" not in line for line in lines):
        detail = [line for line in lines if not line.startswith(("Started ", "jabba-maj-")) and "pam_unix" not in line][
            -6:
        ]
        if not CURRENT_UPDATE["signale"]:
            CURRENT_UPDATE["signale"] = True
            notifications.send(
                "mises_a_jour",
                "Mise à jour échouée",
                f"La version {CURRENT_UPDATE['version']} ne s'est pas installée. Détails dans Système.",
                "/#/systeme",
                family="appli",
            )
        return {"statut": "echec", "version": CURRENT_UPDATE["version"], "detail": detail}
    return {"statut": "termine", "version": CURRENT_UPDATE["version"]}


def update_progress(lines):
    """Étape et pourcentage, lus dans ce que la mise à jour écrit au fur et à mesure."""
    text = "\n".join(lines)
    if re.search(r"Container \S+\s+(Recreate|Starting|Started|Running)", text):
        return {"etape": "Redémarrage de l'appli", "pourcent": 92}
    steps = re.findall(r"\[(?:[\w-]+ )?(\d+)/(\d+)\]", text)
    if steps:
        done_steps, total_steps = (int(version) for version in steps[-1])
        return {
            "etape": f"Construction de l'appli (étape {done_steps} sur {total_steps})",
            "pourcent": 20 + round(65 * done_steps / max(total_steps, 1)),
        }
    if "Signature valable" in text and not re.search(r"Building|building|load build definition", text):
        return {"etape": "Signature vérifiée", "pourcent": 15}
    if re.search(r"Building|building|load build definition|resolve image config", text):
        return {"etape": "Construction de l'appli", "pourcent": 20}
    return {"etape": "Téléchargement de la nouvelle version", "pourcent": 8}
