"""Accessibilité (WCAG 2.2 niveau AA) de chaque écran, vérifiée par axe, sur ordinateur et sur iPhone.

axe est lu dans tests/outils_js/node_modules (npm ci --prefix tests/outils_js). Il est injecté
par le protocole du navigateur : la politique de sécurité de la page n'est pas affaiblie pour le test.
"""

import os
import urllib.parse

import pytest
from playwright.sync_api import expect
from test_ecrans import ECRANS

AXE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outils_js", "node_modules", "axe-core", "axe.min.js"
)
NORMES = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def analyser(page):
    if not page.evaluate("typeof window.axe !== 'undefined'"):
        page.evaluate(open(AXE).read() + "\n;0")
    resultat = page.evaluate(
        """async (normes) => {
          const r = await axe.run(document, {runOnly: {type: "tag", values: normes}, resultTypes: ["violations"]});
          return r.violations.map(v => ({id: v.id, aide: v.help, impact: v.impact,
             cibles: v.nodes.slice(0, 4).map(n => n.target.join(" ") + " → " + (n.failureSummary || "").split("\\n").slice(1, 2).join(""))}));
        }""",
        NORMES,
    )
    return resultat


def decrire(ecran, violations):
    lignes = [f"{ecran} : {v['id']} ({v['impact']}) {v['aide']}\n    " + "\n    ".join(v["cibles"]) for v in violations]
    return "\n".join(lignes)


@pytest.mark.skipif(not os.path.exists(AXE), reason="axe absent : npm ci --prefix tests/outils_js")
def test_chaque_ecran_respecte_wcag_22_aa(page):
    problemes = []
    for ecran in ECRANS + ["fichiers?chemin=" + urllib.parse.quote("/Films et séries/Films")]:
        page.goto(page.racine + "/#/" + ecran)
        page.wait_for_timeout(700)
        expect(page.locator("#contenu h1").first).to_be_visible()
        violations = analyser(page)
        if violations:
            problemes.append(decrire(ecran, violations))
    assert not problemes, "\n".join(problemes)


PETITES_CIBLES = """() => [...document.querySelectorAll('a, button, input, select, summary, [role=button]')]
  .filter(e => !(e.tagName === 'A' && e.closest('p, li, dd, .note')))  // lien dans une phrase : exempté
  .filter(e => { const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none' && e.offsetParent; })
  .map(e => ({e, r: ((e.tagName === 'INPUT' && e.closest('label')) || e).getBoundingClientRect()}))  // une case dans son libellé : on clique sur tout le libellé
  .filter(({r}) => r.width > 0 && r.height > 0 && (r.width < 24 || r.height < 24))
  .map(({e, r}) => `${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}.${[...e.classList].join('.')} « ${(e.textContent || e.getAttribute('aria-label') || '').trim().slice(0, 20)} » ${Math.round(r.width)}×${Math.round(r.height)}`)"""


@pytest.mark.skipif(not os.path.exists(AXE), reason="axe absent : npm ci --prefix tests/outils_js")
@pytest.mark.parametrize("style", ["machines", "atelier"])
@pytest.mark.parametrize("theme", ["clair", "sombre"])
def test_contrastes_des_styles_alternatifs(page, style, theme):
    """Les styles alternatifs gardent des contrastes suffisants, en clair comme en sombre."""
    page.add_init_script(
        f"localStorage.setItem('jabba-style', '{style}'); localStorage.setItem('jabba-theme', '{theme}')"
    )
    for ecran in ("accueil", "outils", "systeme"):
        page.goto(page.racine + "/#/" + ecran)
        page.wait_for_timeout(800)
        violations = [v for v in analyser(page) if v["id"] == "color-contrast"]
        assert not violations, f"{style}/{theme}/{ecran} : {violations}"


def test_cibles_d_au_moins_24_px(page):
    """WCAG 2.5.8 : tout ce qui se touche fait au moins 24 × 24 px (les liens dans une phrase sont exemptés)."""
    trop_petites = []
    for ecran in ECRANS + ["fichiers?chemin=" + urllib.parse.quote("/Films et séries/Films")]:
        page.goto(page.racine + "/#/" + ecran)
        page.wait_for_timeout(600)
        for cible in page.evaluate(PETITES_CIBLES):
            trop_petites.append(f"{ecran} : {cible}")
    assert not trop_petites, "\n".join(sorted(set(trop_petites)))


def test_fenetres_et_theme_sombre(page):
    """Les fenêtres (menu, nouveau dossier) et le thème sombre respectent aussi WCAG 2.2 AA."""
    page.goto(page.racine + "/#/fichiers?chemin=" + urllib.parse.quote("/Films et séries/Films"))
    page.locator(".btn-menu").first.click()
    expect(page.locator("dialog[open]")).to_be_visible()
    assert not analyser(page), decrire("menu •••", analyser(page))
    page.keyboard.press("Escape")
    page.locator(".btn-menu").first.click()
    page.locator("dialog[open] button", has_text="Déplacer vers…").click()
    expect(page.locator("dialog[open] .ligne-dossier").first).to_be_visible()
    assert not analyser(page), decrire("sélecteur de dossier", analyser(page))
    page.keyboard.press("Escape")
    page.click("#nouveau")
    expect(page.locator("dialog[open] input")).to_be_visible()
    assert not analyser(page), decrire("nouveau dossier", analyser(page))
    page.keyboard.press("Escape")
    page.emulate_media(color_scheme="dark")
    problemes = []
    for ecran in ["accueil", "outils", "fichiers", "systeme", "invites"]:
        page.goto(page.racine + "/#/" + ecran)
        page.wait_for_timeout(600)
        violations = analyser(page)
        if violations:
            problemes.append(decrire(ecran + " (sombre)", violations))
    assert not problemes, "\n".join(problemes)


def test_le_focus_n_est_pas_cache_sous_la_barre(page):
    """WCAG 2.4.11 : le dernier élément de la liste, pris au clavier, reste au-dessus de la barre d'onglets."""
    if page.appareil != "iphone":
        pytest.skip("la barre d'onglets n'existe que sur téléphone")
    page.goto(page.racine + "/#/systeme")
    page.wait_for_timeout(800)
    for selecteur in ["#contenu a, #contenu button"]:
        derniers = page.locator(selecteur)
        cible = derniers.nth(derniers.count() - 1)
        cible.focus()
        page.wait_for_timeout(200)
        boite = cible.bounding_box()
        barre = page.locator(".onglets").bounding_box()
        assert boite["y"] + boite["height"] <= barre["y"] + 1, f"caché : {boite} sous {barre}"
