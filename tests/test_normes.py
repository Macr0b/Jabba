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
