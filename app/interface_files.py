"""Fichiers de l'interface (JS, CSS, images), pour l'appli et pour la porte des invités.

Après une mise à jour de Jabba, le navigateur ne doit garder aucun ancien module JS ni CSS.

Sommaire :
  InterfaceFiles : comme StaticFiles, avec « no-cache » (revérifié à chaque ouverture, simple 304
                   s'il n'a pas changé), et qui accepte un préfixe de version : /static/v0.14.1/js/…
                   sert /static/js/…
  index_page     : la page d'accueil, dont les liens JS et CSS portent la version de Jabba. Les
                   imports relatifs entre modules en héritent : une nouvelle version = de nouvelles
                   adresses, que le navigateur ne peut pas confondre avec les anciennes.
"""

import os
import re

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from version import VERSION

VERSION_SEGMENT = re.compile(r"v[0-9][0-9A-Za-z.-]{0,30}")
_PAGES = {}


class InterfaceFiles(StaticFiles):
    def get_path(self, scope):
        path = super().get_path(scope)
        first, _, rest = path.partition(os.sep)
        if rest and VERSION_SEGMENT.fullmatch(first):
            return rest
        return path

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def index_page(static_dir):
    """index.html, avec la version dans les adresses des scripts et des feuilles de style."""
    if static_dir not in _PAGES:
        with open(os.path.join(static_dir, "index.html"), encoding="utf-8") as file:
            html = file.read()
        for folder in ("js", "css"):
            html = html.replace(f'"/static/{folder}/', f'"/static/v{VERSION}/{folder}/')
        _PAGES[static_dir] = html
    return HTMLResponse(_PAGES[static_dir], headers={"Cache-Control": "no-cache"})
