"""Code de l'interface : aucun nom inconnu (une faute de frappe ou un import oublié casserait un écran)."""

import glob
import os
import shutil
import subprocess

import pytest
from conftest import APP

TOOLS = os.path.join(os.path.dirname(__file__), "outils_js")


@pytest.mark.skipif(
    not shutil.which("node") or not os.path.isdir(os.path.join(TOOLS, "node_modules")),
    reason="node ou les outils (npm ci --prefix tests/outils_js) absents",
)
def test_no_unknown_name_in_the_interface():
    modules = glob.glob(os.path.join(APP, "static", "js", "**", "*.js"), recursive=True)
    assert modules
    r = subprocess.run(["node", os.path.join(TOOLS, "noms_inconnus.cjs"), *modules], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(
    not shutil.which("node") or not os.path.isdir(os.path.join(TOOLS, "node_modules")), reason="outils absents"
)
def test_the_check_sees_an_unknown_name(tmp_path):
    culprit = tmp_path / "fautif.js"
    culprit.write_text('import { toast } from "./dialogs.js";\nexport function f() { toast(messageInconnu); }\n')
    r = subprocess.run(["node", os.path.join(TOOLS, "noms_inconnus.cjs"), str(culprit)], capture_output=True, text=True)
    assert r.returncode == 1 and "messageInconnu" in r.stdout


def test_text_catalog_up_to_date():
    """Tous les textes de l'interface sont rassemblés dans app/static/locales/fr.json."""
    result = subprocess.run(
        ["node", os.path.join(TOOLS, "extraire_textes.cjs"), "--verifier"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_icons_up_to_date():
    """app/static/vendor/icons.js contient exactement les icônes Tabler listées dans tests/outils_js/icones.cjs."""
    result = subprocess.run(["node", os.path.join(TOOLS, "icones.cjs"), "--verifier"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_every_requested_icon_exists():
    """Chaque outil et chaque écran de core/icons.js pointe vers une icône présente dans vendor/icons.js."""
    icons = open(os.path.join(APP, "static", "vendor", "icons.js")).read()
    requested = open(os.path.join(APP, "static", "js", "core", "icons.js")).read()
    import re

    names = re.findall(r'^\s+"?[\w-]+"?: "([\w-]+)",$', requested, re.M)
    assert len(names) > 20
    for nom in names:
        assert f'"{nom}":' in icons, nom


def test_every_text_goes_through_translation():
    result = subprocess.run(
        ["node", os.path.join(TOOLS, "baliser_textes.cjs"), os.path.join(APP, "static", "js"), "--verifier"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_translation_replaces_and_keeps_values(tmp_path):
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
    output = subprocess.run(["node", str(essai)], capture_output=True, text=True)
    assert output.returncode == 0, output.stderr
    import json

    assert json.loads(output.stdout) == [
        "Cancel",
        "3 files for you, Rob",
        "3 files for you, Rob",
        "Pas traduit",
        "Rien 3",
    ]
