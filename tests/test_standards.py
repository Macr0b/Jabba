"""Les normes du code, vérifiées à chaque essai : chaque fichier commence par un sommaire qui nomme chacune
de ses fonctions, et une seule instruction par ligne (Python : pas de « ; » ; interface : tests/outils_js/normes.cjs)."""

import ast
import io
import os
import re
import subprocess
import tokenize

from conftest import ROOT

APP = os.path.join(ROOT, "app")
TOOLS = os.path.join(ROOT, "tests", "outils_js")


def python_modules():
    for dossier, _, fichiers in os.walk(APP):
        if "__pycache__" in dossier or os.sep + "static" in dossier:
            continue
        for file in fichiers:
            if file.endswith(".py"):
                yield os.path.join(dossier, file)


def names_it(nom, summary):
    """Le nom figure dans le sommaire (avec ou sans le « _ » des fonctions internes)."""
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(nom.lstrip("_")) + r"(?![A-Za-z0-9_])", summary) is not None


def test_every_python_module_has_a_complete_summary():
    gaps = []
    for chemin in python_modules():
        tree = ast.parse(open(chemin).read())
        names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        if not names:
            continue
        doc = ast.get_docstring(tree) or ""
        relative = os.path.relpath(chemin, APP)
        if "Sommaire" not in doc:
            gaps.append(f"{relative} : pas de « Sommaire »")
            continue
        forgotten = [nom for nom in names if not names_it(nom, doc)]
        if forgotten:
            gaps.append(f"{relative} : absent du sommaire : {', '.join(forgotten)}")
    assert not gaps, "\n".join(gaps)


def test_one_statement_per_line_in_python():
    gaps = []
    for chemin in python_modules():
        tokens = tokenize.generate_tokens(io.StringIO(open(chemin).read()).readline)
        gaps += [
            f"{os.path.relpath(chemin, APP)}:{j.start[0]}" for j in tokens if j.type == tokenize.OP and j.string == ";"
        ]
    assert not gaps, "« ; » : " + ", ".join(gaps)


def test_interface_standards():
    result = subprocess.run(
        ["node", os.path.join(TOOLS, "normes.cjs"), os.path.join(APP, "static", "js")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout


def test_the_interface_check_sees_the_gaps(tmp_path):
    (tmp_path / "sans.js").write_text("export function a() {}\n")
    (tmp_path / "charge.js").write_text("// Sommaire : b\nexport function b() { const x = 1, y = 2; f(); g(); }\n")
    result = subprocess.run(["node", os.path.join(TOOLS, "normes.cjs"), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "sans.js : pas de « Sommaire » en tête" in result.stdout
    assert "charge.js:2 plusieurs déclarations d'un coup" in result.stdout
    assert "charge.js:2 deux instructions sur la ligne" in result.stdout


SCRIPTS = os.path.join(APP, "host", "scripts")


def test_server_scripts_pass_shellcheck():
    """Les scripts lancés sur le serveur (avec tous les droits) : des fichiers à part, relus par shellcheck."""
    gaps = []
    for nom in sorted(os.listdir(SCRIPTS)):
        chemin = os.path.join(SCRIPTS, nom)
        first = open(chemin).readline().strip()
        assert nom.endswith(".sh") and first in ("#!/bin/sh", "#!/bin/bash"), f"{nom} : en-tête {first!r}"
        dialect = "bash" if first.endswith("bash") else "sh"
        result = subprocess.run(["shellcheck", "-s", dialect, chemin], capture_output=True, text=True)
        if result.returncode:
            gaps.append(result.stdout)
    assert not gaps, "\n".join(gaps)


def test_every_script_is_used_and_exists():
    """Pas de script oublié dans host/scripts, ni d'appel à un script qui n'existe pas."""
    calls = set()
    for chemin in python_modules():
        calls |= set(re.findall(r'script\("([a-z_]+)"\)', open(chemin).read()))
    fichiers = {nom.removesuffix(".sh") for nom in os.listdir(SCRIPTS)}
    assert calls - fichiers == set() and fichiers - calls == set()


def test_no_long_shell_in_the_python():
    """Un texte de shell de plus de 3 lignes dans le Python : il va dans host/scripts."""
    gaps = []
    for chemin in python_modules():
        for tree_node in ast.walk(ast.parse(open(chemin).read())):
            if (
                isinstance(tree_node, ast.Constant)
                and isinstance(tree_node.value, str)
                and tree_node.value.count("\n") > 3
            ):
                texte = tree_node.value
                if re.search(r"^\s*(set -e|if \[|for \w+ in|fi$|done$)", texte, re.M):
                    gaps.append(f"{os.path.relpath(chemin, APP)}:{tree_node.lineno}")
    assert not gaps, "shell à sortir dans host/scripts : " + ", ".join(gaps)


def test_no_python_function_over_50_lines():
    """Au-delà, une fonction se découpe en morceaux nommés : plus facile à lire et à essayer."""
    long_ones = []
    for chemin in python_modules():
        for tree_node in ast.walk(ast.parse(open(chemin).read())):
            if (
                isinstance(tree_node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and tree_node.end_lineno - tree_node.lineno >= 50
            ):
                long_ones.append(f"{os.path.relpath(chemin, APP)}:{tree_node.lineno} {tree_node.name}")
    assert not long_ones, ", ".join(long_ones)


def test_style_sheets_one_responsibility_each():
    """Chaque feuille commence par dire ce qu'elle habille, reste courte, et est chargée par la page."""
    dossier = os.path.join(APP, "static", "css")
    page = open(os.path.join(APP, "static", "index.html")).read()
    for nom in sorted(os.listdir(dossier)):
        texte = open(os.path.join(dossier, nom)).read()
        assert texte.startswith("/*"), f"{nom} : pas de phrase d'en-tête"
        assert texte.count("\n") <= 450, f"{nom} : plus de 450 lignes, à séparer"
        assert f'href="/static/css/{nom}"' in page, f"{nom} n'est pas chargée"
