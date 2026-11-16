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
SIGNATURE_NAMESPACE = "jabba"   # « namespace » de ssh-keygen -Y : une signature faite pour autre chose ne vaut rien ici
REFUSAL_MARK = "JABBA-SIGNATURE-REFUSEE"


def publication_key():
    """La clé publique qui signe les versions (celle de la version installée), ou None."""
    try:
        with open(KEY_FILE) as f:
            lignes = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except OSError:
        return None
    return lignes[0] if lignes and RE_KEY.match(lignes[0]) else None


def signed_text(tag, commit):
    return f"jabba {tag} {commit}"
_CACHE = {"t": 0, "v": None}


def version_tuple(v):
    m = RE_VERSION.match(v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def latest_release(depot, forcer=False):
    """{'version': '0.5.1', 'tag': 'v0.5.1', 'notes': '...', 'date': '...'} ou None."""
    if not depot or not re.fullmatch(r"[\w.-]+/[\w.-]+", depot):
        return None
    if not forcer and time.time() - _CACHE["t"] < 6 * 3600 and _CACHE["v"] is not None:
        return _CACHE["v"]
    try:
        r = httpx.get(f"https://api.github.com/repos/{depot}/releases/latest", timeout=15,
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "jabba-app"})
    except httpx.HTTPError:
        return _CACHE["v"]
    if r.status_code != 200:
        return _CACHE["v"]
    d = r.json()
    tag = d.get("tag_name", "")
    if not version_tuple(tag):
        return None
    v = {"version": tag.lstrip("v"), "tag": tag, "notes": (d.get("body") or "")[:3000], "date": d.get("published_at")}
    _CACHE.update(t=time.time(), v=v)
    return v


def is_newer(actuelle, autre):
    a, b = version_tuple(actuelle), version_tuple(autre)
    return bool(a and b and b > a)


def install_script(dossier, tag, cle):
    """Commande lancée sur le serveur, hors du conteneur (elle survit au redémarrage de l'appli).

    Étapes : récupérer le dépôt, vérifier la signature de cette version avec `cle`
    (la clé de la version installée), puis installer exactement le commit signé.
    """
    if not re.fullmatch(r"/[\w./-]+", dossier) or not RE_VERSION.match(tag):
        raise ValueError("Paramètres inattendus")
    if not cle or not RE_KEY.match(cle):
        raise ValueError("Pas de clé de publication : mise à jour impossible")
    # git tourne avec le compte propriétaire du dossier : les fichiers restent à son nom.
    # Avant, on lui rend tout ce qui ne lui appartient plus (traces d'anciennes installations
    # faites en root), sauf donnees/ qui reste à l'appli.
    proprio = f"$(stat -c %U:%G '{dossier}')"
    remettre = (f"find '{dossier}' -path '{dossier}/donnees' -prune -o ! -user \"$(stat -c %U '{dossier}')\" "
                f"-exec chown -h \"{proprio}\" {{}} +")
    git = f"runuser -u \"$(stat -c %U '{dossier}')\" -- git"
    # Le fichier de signature de cette version, sur main : « jabba <tag> <commit> » puis la signature.
    # Toute étape qui échoue (fichier absent, ligne inattendue, signature fausse) = refus.
    verifier = (
        'T=$(mktemp -d) && trap \'rm -rf "$T"\' EXIT && '
        f"printf '%s %s\\n' {SIGNATURE_NAMESPACE} '{cle}' > \"$T/cles\" && "
        f"if {git} show 'refs/remotes/origin/main:signatures/{tag}.sig' > \"$T/fichier\" 2>/dev/null"
        ' && LIGNE=$(head -n 1 "$T/fichier")'
        f' && [ "${{LIGNE% *}}" = "jabba {tag}" ] && SHA="${{LIGNE##* }}"'
        ' && printf %s "$SHA" | grep -Eqx "[0-9a-f]{40}"'
        " && sed -n '/^-----BEGIN SSH SIGNATURE-----$/,/^-----END SSH SIGNATURE-----$/p' \"$T/fichier\" > \"$T/sig\""
        f' && printf %s "$LIGNE" | ssh-keygen -Y verify -f "$T/cles" -I {SIGNATURE_NAMESPACE} -n {SIGNATURE_NAMESPACE} -s "$T/sig"'
        f' && {git} cat-file -e "$SHA^{{commit}}"; then echo "Signature valable : {tag} ($SHA)"; '
        f"else echo '{REFUSAL_MARK} : la version {tag} n est pas signée par la clé attendue, rien n a été installé.'; exit 1; fi"
    )
    return (f"cd '{dossier}' && {remettre} && {git} fetch --quiet --tags --force origin && {verifier} && "
            f"{git} -c advice.detachedHead=false checkout --quiet --force \"$SHA\" && "
            "docker compose up -d --build && docker image prune -f")


# ---- ajouté au découpage de main.py
import os
import re
from services import notifications
import config
from host import commands


# ---------------------------------------------------------------- mise à jour de l'appli
def app_folder():
    code, sortie, _ = commands.run_on_host(["docker", "inspect", "-f", '{{index .Config.Labels "com.docker.compose.project.working_dir"}}', "jabba-app"])
    d = sortie.strip()
    return d if code == 0 and re.fullmatch(r"/[\w./-]+", d) else None


CURRENT_UPDATE = {"unite": None, "version": None, "debut": 0, "signale": False}


def update_state():
    """Où en est la dernière installation lancée : en_cours, echec (avec le détail) ou termine."""
    if not CURRENT_UPDATE["unite"]:
        return {"statut": "aucune"}
    u = CURRENT_UPDATE["unite"] + ".service"
    _, actif, _ = commands.run_on_host(["systemctl", "show", "-p", "ActiveState", "--value", u])
    _, log, _ = commands.run_on_host(["journalctl", "-u", u, "--no-pager", "-o", "cat", "-n", "300"])
    lignes = [l for l in log.splitlines() if l.strip()]
    if actif.strip() in ("active", "activating", "reloading"):
        return {"statut": "en_cours", "version": CURRENT_UPDATE["version"], "depuis": int(time.time() - CURRENT_UPDATE["debut"]), **update_progress(lignes)}
    if any(REFUSAL_MARK in l for l in lignes):
        if not CURRENT_UPDATE["signale"]:
            CURRENT_UPDATE["signale"] = True
            notifications.send("securite", "Mise à jour refusée",
                           f"La version {CURRENT_UPDATE['version']} publiée sur GitHub n'est pas signée par la clé attendue : rien n'a été installé. "
                           "Si tu n'as rien publié toi-même, ton compte GitHub est peut-être piraté.", "/#/systeme", famille="appli")
        return {"statut": "echec", "version": CURRENT_UPDATE["version"],
                "detail": ["Signature refusée : cette version n'est pas signée par la clé attendue. Rien n'a été installé, l'appli actuelle continue."]}
    if any("Failed with result" in l or "status=" in l and "status=0/" not in l for l in lignes):
        detail = [l for l in lignes if not l.startswith(("Started ", "jabba-maj-")) and "pam_unix" not in l][-6:]
        if not CURRENT_UPDATE["signale"]:
            CURRENT_UPDATE["signale"] = True
            notifications.send("mises_a_jour", "Mise à jour échouée",
                           f"La version {CURRENT_UPDATE['version']} ne s'est pas installée. Détails dans Système.", "/#/systeme", famille="appli")
        return {"statut": "echec", "version": CURRENT_UPDATE["version"], "detail": detail}
    return {"statut": "termine", "version": CURRENT_UPDATE["version"]}


def update_progress(lignes):
    """Étape et pourcentage, lus dans ce que la mise à jour écrit au fur et à mesure."""
    texte = "\n".join(lignes)
    if re.search(r"Container \S+\s+(Recreate|Starting|Started|Running)", texte):
        return {"etape": "Redémarrage de l'appli", "pourcent": 92}
    etapes = re.findall(r"\[(?:[\w-]+ )?(\d+)/(\d+)\]", texte)
    if etapes:
        x, y = (int(v) for v in etapes[-1])
        return {"etape": f"Construction de l'appli (étape {x} sur {y})", "pourcent": 20 + round(65 * x / max(y, 1))}
    if "Signature valable" in texte and not re.search(r"Building|building|load build definition", texte):
        return {"etape": "Signature vérifiée", "pourcent": 15}
    if re.search(r"Building|building|load build definition|resolve image config", texte):
        return {"etape": "Construction de l'appli", "pourcent": 20}
    return {"etape": "Téléchargement de la nouvelle version", "pourcent": 8}
