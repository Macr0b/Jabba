"""Mise à jour de l'appli depuis son dépôt GitHub public.

Le serveur regarde la dernière version publiée (une « release » vX.Y.Z).
Rien ne s'installe tout seul : l'admin clique, le serveur télécharge le code
de cette version, reconstruit l'appli et la relance. .env et donnees/ ne sont
jamais touchés (ils ne sont pas dans le dépôt).
"""
import re
import time

import httpx

RE_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
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


def script(dossier, tag):
    """Commande lancée sur le serveur, hors du conteneur (elle survit au redémarrage de l'appli)."""
    if not re.fullmatch(r"/[\w./-]+", dossier) or not RE_VERSION.match(tag):
        raise ValueError("Paramètres inattendus")
    # git tourne avec le compte propriétaire du dossier : les fichiers restent à son nom.
    # Avant, on lui rend tout ce qui ne lui appartient plus (traces d'anciennes installations
    # faites en root), sauf donnees/ qui reste à l'appli.
    proprio = f"$(stat -c %U:%G '{dossier}')"
    remettre = (f"find '{dossier}' -path '{dossier}/donnees' -prune -o ! -user \"$(stat -c %U '{dossier}')\" "
                f"-exec chown -h \"{proprio}\" {{}} +")
    git = f"runuser -u \"$(stat -c %U '{dossier}')\" -- git"
    return (f"cd '{dossier}' && {remettre} && {git} fetch --quiet --tags --force origin && "
            f"{git} -c advice.detachedHead=false checkout --quiet --force 'refs/tags/{tag}' && "
            "docker compose up -d --build && docker image prune -f")
