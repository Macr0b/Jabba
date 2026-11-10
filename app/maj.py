"""Mise à jour de l'appli depuis son dépôt GitHub public.

Le serveur regarde la dernière version publiée (une « release » vX.Y.Z).
Rien ne s'installe tout seul : l'admin clique, le serveur télécharge le code
de cette version, reconstruit l'appli et la relance. .env et donnees/ ne sont
jamais touchés (ils ne sont pas dans le dépôt).

Signature : chaque version publiée porte, dans le message de son étiquette (tag),
une signature SSH faite avec une clé qui ne quitte pas l'ordinateur du mainteneur.
Le texte signé est « jabba <tag> <commit> ». Avant d'installer quoi que ce soit, le
serveur vérifie cette signature avec la clé publique de la version DÉJÀ installée
(cle_publication.pub, figée dans l'appli qui tourne) : un compte GitHub piraté ne
suffit pas à faire installer du code. Sans signature valable, rien ne s'installe.
"""
import os
import re
import time

import httpx

RE_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
FICHIER_CLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cle_publication.pub")
RE_CLE = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) [A-Za-z0-9+/=]{40,800}( [\w@.+ -]{0,100})?$")
ESPACE_SIGNATURE = "jabba"   # « namespace » de ssh-keygen -Y : une signature faite pour autre chose ne vaut rien ici
MARQUE_REFUS = "JABBA-SIGNATURE-REFUSEE"


def cle_publication():
    """La clé publique qui signe les versions (celle de la version installée), ou None."""
    try:
        with open(FICHIER_CLE) as f:
            lignes = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except OSError:
        return None
    return lignes[0] if lignes and RE_CLE.match(lignes[0]) else None


def texte_signe(tag, commit):
    return f"jabba {tag} {commit}"
_CACHE = {"t": 0, "v": None}


def tuple_version(v):
    m = RE_VERSION.match(v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def derniere(depot, forcer=False):
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
    if not tuple_version(tag):
        return None
    v = {"version": tag.lstrip("v"), "tag": tag, "notes": (d.get("body") or "")[:3000], "date": d.get("published_at")}
    _CACHE.update(t=time.time(), v=v)
    return v


def plus_recente(actuelle, autre):
    a, b = tuple_version(actuelle), tuple_version(autre)
    return bool(a and b and b > a)


def script(dossier, tag, cle):
    """Commande lancée sur le serveur, hors du conteneur (elle survit au redémarrage de l'appli).

    Étapes : récupérer les étiquettes, vérifier la signature de celle-ci avec `cle`
    (la clé de la version installée), puis installer exactement le commit signé.
    """
    if not re.fullmatch(r"/[\w./-]+", dossier) or not RE_VERSION.match(tag):
        raise ValueError("Paramètres inattendus")
    if not cle or not RE_CLE.match(cle):
        raise ValueError("Pas de clé de publication : mise à jour impossible")
    # git tourne avec le compte propriétaire du dossier : les fichiers restent à son nom.
    # Avant, on lui rend tout ce qui ne lui appartient plus (traces d'anciennes installations
    # faites en root), sauf donnees/ qui reste à l'appli.
    proprio = f"$(stat -c %U:%G '{dossier}')"
    remettre = (f"find '{dossier}' -path '{dossier}/donnees' -prune -o ! -user \"$(stat -c %U '{dossier}')\" "
                f"-exec chown -h \"{proprio}\" {{}} +")
    git = f"runuser -u \"$(stat -c %U '{dossier}')\" -- git"
    verifier = (
        'T=$(mktemp -d) && trap \'rm -rf "$T"\' EXIT && '
        f"printf '%s %s\\n' {ESPACE_SIGNATURE} '{cle}' > \"$T/cles\" && "
        f"SHA=$({git} rev-parse --verify 'refs/tags/{tag}^{{commit}}') && "
        f"{git} tag -l --format='%(contents)' '{tag}' | "
        "sed -n '/^-----BEGIN SSH SIGNATURE-----$/,/^-----END SSH SIGNATURE-----$/p' > \"$T/sig\" && "
        f"if printf '%s' \"jabba {tag} $SHA\" | ssh-keygen -Y verify -f \"$T/cles\" -I {ESPACE_SIGNATURE} "
        f"-n {ESPACE_SIGNATURE} -s \"$T/sig\"; then echo \"Signature valable : {tag} ($SHA)\"; "
        f"else echo '{MARQUE_REFUS} : la version {tag} n est pas signée par la clé attendue, rien n a été installé.'; exit 1; fi"
    )
    return (f"cd '{dossier}' && {remettre} && {git} fetch --quiet --tags --force origin && {verifier} && "
            f"{git} -c advice.detachedHead=false checkout --quiet --force \"$SHA\" && "
            "docker compose up -d --build && docker image prune -f")
