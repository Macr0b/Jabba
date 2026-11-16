"""Code de l'interface : aucun nom inconnu (une faute de frappe ou un import oublié casserait un écran)."""

import glob
import os
import shutil
import subprocess

import pytest
from conftest import APP

OUTILS = os.path.join(os.path.dirname(__file__), "outils_js")


@pytest.mark.skipif(
    not shutil.which("node") or not os.path.isdir(os.path.join(OUTILS, "node_modules")),
    reason="node ou les outils (npm ci --prefix tests/outils_js) absents",
)
def test_aucun_nom_inconnu_dans_l_interface():
    modules = glob.glob(os.path.join(APP, "static", "js", "**", "*.js"), recursive=True)
    assert modules
    r = subprocess.run(["node", os.path.join(OUTILS, "noms_inconnus.cjs"), *modules], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(not shutil.which("node") or not os.path.isdir(os.path.join(OUTILS, "node_modules")), reason="outils absents")
def test_la_verification_voit_un_nom_inconnu(tmp_path):
    fautif = tmp_path / "fautif.js"
    fautif.write_text('import { toast } from "./dialogs.js";\nexport function f() { toast(messageInconnu); }\n')
    r = subprocess.run(["node", os.path.join(OUTILS, "noms_inconnus.cjs"), str(fautif)], capture_output=True, text=True)
    assert r.returncode == 1 and "messageInconnu" in r.stdout
