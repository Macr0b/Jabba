"""Les normes du code, vérifiées à chaque essai : chaque fichier commence par un sommaire qui nomme chacune
de ses fonctions, et une seule instruction par ligne (Python : pas de « ; » ; interface : tests/outils_js/normes.cjs)."""

import ast
import io
import os
import re
import subprocess
import tokenize

from conftest import RACINE

APP = os.path.join(RACINE, "app")
OUTILS = os.path.join(RACINE, "tests", "outils_js")


def modules_python():
    for dossier, _, fichiers in os.walk(APP):
        if "__pycache__" in dossier or os.sep + "static" in dossier:
            continue
        for fichier in fichiers:
            if fichier.endswith(".py"):
                yield os.path.join(dossier, fichier)


def nomme(nom, sommaire):
    """Le nom figure dans le sommaire (avec ou sans le « _ » des fonctions internes)."""
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(nom.lstrip("_")) + r"(?![A-Za-z0-9_])", sommaire) is not None


def test_chaque_module_python_a_un_sommaire_complet():
    ecarts = []
    for chemin in modules_python():
        arbre = ast.parse(open(chemin).read())
        noms = [n.name for n in arbre.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        if not noms:
            continue
        doc = ast.get_docstring(arbre) or ""
        relatif = os.path.relpath(chemin, APP)
        if "Sommaire" not in doc:
            ecarts.append(f"{relatif} : pas de « Sommaire »")
            continue
        oublies = [nom for nom in noms if not nomme(nom, doc)]
        if oublies:
            ecarts.append(f"{relatif} : absent du sommaire : {', '.join(oublies)}")
    assert not ecarts, "\n".join(ecarts)


def test_une_instruction_par_ligne_en_python():
    ecarts = []
    for chemin in modules_python():
        jetons = tokenize.generate_tokens(io.StringIO(open(chemin).read()).readline)
        ecarts += [
            f"{os.path.relpath(chemin, APP)}:{j.start[0]}" for j in jetons if j.type == tokenize.OP and j.string == ";"
        ]
    assert not ecarts, "« ; » : " + ", ".join(ecarts)


def test_normes_de_l_interface():
    resultat = subprocess.run(
        ["node", os.path.join(OUTILS, "normes.cjs"), os.path.join(APP, "static", "js")], capture_output=True, text=True
    )
    assert resultat.returncode == 0, resultat.stdout


def test_le_controle_de_l_interface_voit_les_ecarts(tmp_path):
    (tmp_path / "sans.js").write_text("export function a() {}\n")
    (tmp_path / "charge.js").write_text("// Sommaire : b\nexport function b() { const x = 1, y = 2; f(); g(); }\n")
    resultat = subprocess.run(
        ["node", os.path.join(OUTILS, "normes.cjs"), str(tmp_path)], capture_output=True, text=True
    )
    assert resultat.returncode == 1
    assert "sans.js : pas de « Sommaire » en tête" in resultat.stdout
    assert "charge.js:2 plusieurs déclarations d'un coup" in resultat.stdout
    assert "charge.js:2 deux instructions sur la ligne" in resultat.stdout


SCRIPTS = os.path.join(APP, "host", "scripts")


def test_les_scripts_du_serveur_passent_shellcheck():
    """Les scripts lancés sur le serveur (avec tous les droits) : des fichiers à part, relus par shellcheck."""
    ecarts = []
    for nom in sorted(os.listdir(SCRIPTS)):
        chemin = os.path.join(SCRIPTS, nom)
        premiere = open(chemin).readline().strip()
        assert nom.endswith(".sh") and premiere in ("#!/bin/sh", "#!/bin/bash"), f"{nom} : en-tête {premiere!r}"
        dialecte = "bash" if premiere.endswith("bash") else "sh"
        resultat = subprocess.run(["shellcheck", "-s", dialecte, chemin], capture_output=True, text=True)
        if resultat.returncode:
            ecarts.append(resultat.stdout)
    assert not ecarts, "\n".join(ecarts)


def test_chaque_script_sert_et_existe():
    """Pas de script oublié dans host/scripts, ni d'appel à un script qui n'existe pas."""
    appels = set()
    for chemin in modules_python():
        appels |= set(re.findall(r'script\("([a-z_]+)"\)', open(chemin).read()))
    fichiers = {nom.removesuffix(".sh") for nom in os.listdir(SCRIPTS)}
    assert appels - fichiers == set() and fichiers - appels == set()


def test_plus_de_long_shell_dans_le_python():
    """Un texte de shell de plus de 3 lignes dans le Python : il va dans host/scripts."""
    ecarts = []
    for chemin in modules_python():
        for noeud in ast.walk(ast.parse(open(chemin).read())):
            if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str) and noeud.value.count("\n") > 3:
                texte = noeud.value
                if re.search(r"^\s*(set -e|if \[|for \w+ in|fi$|done$)", texte, re.M):
                    ecarts.append(f"{os.path.relpath(chemin, APP)}:{noeud.lineno}")
    assert not ecarts, "shell à sortir dans host/scripts : " + ", ".join(ecarts)


def test_aucune_fonction_python_de_plus_de_50_lignes():
    """Au-delà, une fonction se découpe en morceaux nommés : plus facile à lire et à essayer."""
    longues = []
    for chemin in modules_python():
        for noeud in ast.walk(ast.parse(open(chemin).read())):
            if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)) and noeud.end_lineno - noeud.lineno >= 50:
                longues.append(f"{os.path.relpath(chemin, APP)}:{noeud.lineno} {noeud.name}")
    assert not longues, ", ".join(longues)


def test_feuilles_de_style_une_responsabilite_chacune():
    """Chaque feuille commence par dire ce qu'elle habille, reste courte, et est chargée par la page."""
    dossier = os.path.join(APP, "static", "css")
    page = open(os.path.join(APP, "static", "index.html")).read()
    for nom in sorted(os.listdir(dossier)):
        texte = open(os.path.join(dossier, nom)).read()
        assert texte.startswith("/*"), f"{nom} : pas de phrase d'en-tête"
        assert texte.count("\n") <= 450, f"{nom} : plus de 450 lignes, à séparer"
        assert f'href="/static/css/{nom}"' in page, f"{nom} n'est pas chargée"
