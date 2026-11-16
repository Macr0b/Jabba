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


@pytest.mark.skipif(
    not shutil.which("node") or not os.path.isdir(os.path.join(OUTILS, "node_modules")), reason="outils absents"
)
def test_la_verification_voit_un_nom_inconnu(tmp_path):
    fautif = tmp_path / "fautif.js"
    fautif.write_text('import { toast } from "./dialogs.js";\nexport function f() { toast(messageInconnu); }\n')
    r = subprocess.run(["node", os.path.join(OUTILS, "noms_inconnus.cjs"), str(fautif)], capture_output=True, text=True)
    assert r.returncode == 1 and "messageInconnu" in r.stdout


def test_catalogue_des_textes_a_jour():
    """Tous les textes de l'interface sont rassemblés dans app/static/locales/fr.json."""
    resultat = subprocess.run(
        ["node", os.path.join(OUTILS, "extraire_textes.cjs"), "--verifier"], capture_output=True, text=True
    )
    assert resultat.returncode == 0, resultat.stderr


def test_tous_les_textes_passent_par_la_traduction():
    resultat = subprocess.run(
        ["node", os.path.join(OUTILS, "baliser_textes.cjs"), os.path.join(APP, "static", "js"), "--verifier"],
        capture_output=True,
        text=True,
    )
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr


def test_la_traduction_remplace_et_garde_les_valeurs(tmp_path):
    """Avec un catalogue « xx » : texte traduit, valeurs remises à leur place, texte absent laissé en français."""
    essai = tmp_path / "essai.mjs"
    module = os.path.join(APP, "static", "js", "core", "i18n.js")
    essai.write_text(
        f"""
globalThis.localStorage = {{ getItem: () => "xx", setItem() {{}} }};
Object.defineProperty(globalThis, "navigator", {{ value: {{ language: "xx" }}, configurable: true }});
globalThis.document = {{ documentElement: {{}} }};
globalThis.fetch = async () => ({{ ok: true, json: async () => ({{
  "Annuler": "Cancel",
  "Bonjour {{0}}, tu as {{1}} fichiers": "{{1}} files for you, {{0}}",
}}) }});
const source = (await import("node:fs")).readFileSync({module!r}, "utf8").replace('{{ fr: "Français" }}', '{{ fr: "Français", xx: "Essai" }}');
const {{ t }} = await import("data:text/javascript," + encodeURIComponent(source));
const nom = "Rob";
const n = 3;
console.log(JSON.stringify([t("Annuler"), t`Bonjour ${{nom}}, tu as ${{n}} fichiers`, t`Bonjour
   ${{nom}},   tu as ${{n}} fichiers`, t("Pas traduit"), t`Rien ${{n}}`]));
"""
    )
    sortie = subprocess.run(["node", str(essai)], capture_output=True, text=True)
    assert sortie.returncode == 0, sortie.stderr
    import json

    assert json.loads(sortie.stdout) == [
        "Cancel",
        "3 files for you, Rob",
        "3 files for you, Rob",
        "Pas traduit",
        "Rien 3",
    ]
