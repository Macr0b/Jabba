"""Accessibilité (WCAG 2.2 niveau AA) de chaque écran, vérifiée par axe, sur ordinateur et sur iPhone.

axe est lu dans tests/outils_js/node_modules (npm ci --prefix tests/outils_js). Il est injecté
par le protocole du navigateur : la politique de sécurité de la page n'est pas affaiblie pour le test.
"""

import os
import urllib.parse

import pytest
from playwright.sync_api import expect
from test_screens import SCREENS_

AXE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outils_js", "node_modules", "axe-core", "axe.min.js"
)
STANDARDS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def scan(page):
    if not page.evaluate("typeof window.axe !== 'undefined'"):
        page.evaluate(open(AXE).read() + "\n;0")
    result = page.evaluate(
        """async (normes) => {
          const r = await axe.run(document, {runOnly: {type: "tag", values: normes}, resultTypes: ["violations"]});
          return r.violations.map(v => ({id: v.id, aide: v.help, impact: v.impact,
             cibles: v.nodes.slice(0, 4).map(n => n.target.join(" ") + " → " + (n.failureSummary || "").split("\\n").slice(1, 2).join(""))}));
        }""",
        STANDARDS,
    )
    return result


def describe(ecran, violations):
    lines = [f"{ecran} : {v['id']} ({v['impact']}) {v['aide']}\n    " + "\n    ".join(v["cibles"]) for v in violations]
    return "\n".join(lines)


@pytest.mark.skipif(not os.path.exists(AXE), reason="axe absent : npm ci --prefix tests/outils_js")
def test_every_screen_meets_wcag_22_aa(page):
    problems = []
    for ecran in SCREENS_ + ["fichiers?chemin=" + urllib.parse.quote("/Films et séries/Films")]:
        page.goto(page.root + "/#/" + ecran)
        page.wait_for_timeout(700)
        expect(page.locator("#contenu h1").first).to_be_visible()
        violations = scan(page)
        if violations:
            problems.append(describe(ecran, violations))
    assert not problems, "\n".join(problems)


SMALL_TARGETS = """() => [...document.querySelectorAll('a, button, input, select, summary, [role=button]')]
  .filter(e => !(e.tagName === 'A' && e.closest('p, li, dd, .note')))  // lien dans une phrase : exempté
  .filter(e => { const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none' && e.offsetParent; })
  .map(e => ({e, r: ((e.tagName === 'INPUT' && e.closest('label')) || e).getBoundingClientRect()}))  // une case dans son libellé : on clique sur tout le libellé
  .filter(({r}) => r.width > 0 && r.height > 0 && (r.width < 24 || r.height < 24))
  .map(({e, r}) => `${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}.${[...e.classList].join('.')} « ${(e.textContent || e.getAttribute('aria-label') || '').trim().slice(0, 20)} » ${Math.round(r.width)}×${Math.round(r.height)}`)"""


@pytest.mark.skipif(not os.path.exists(AXE), reason="axe absent : npm ci --prefix tests/outils_js")
@pytest.mark.parametrize("style", ["machines", "atelier"])
@pytest.mark.parametrize("theme", ["clair", "sombre"])
def test_contrast_of_alternative_styles(page, style, theme):
    """Les styles alternatifs gardent des contrastes suffisants, en clair comme en sombre."""
    page.add_init_script(
        f"localStorage.setItem('jabba-style', '{style}'); localStorage.setItem('jabba-theme', '{theme}')"
    )
    for ecran in ("accueil", "outils", "systeme"):
        page.goto(page.root + "/#/" + ecran)
        page.wait_for_timeout(800)
        violations = [v for v in scan(page) if v["id"] == "color-contrast"]
        assert not violations, f"{style}/{theme}/{ecran} : {violations}"


def test_targets_at_least_24_px(page):
    """WCAG 2.5.8 : tout ce qui se touche fait au moins 24 × 24 px (les liens dans une phrase sont exemptés)."""
    too_small = []
    for ecran in SCREENS_ + ["fichiers?chemin=" + urllib.parse.quote("/Films et séries/Films")]:
        page.goto(page.root + "/#/" + ecran)
        page.wait_for_timeout(600)
        for target in page.evaluate(SMALL_TARGETS):
            too_small.append(f"{ecran} : {target}")
    assert not too_small, "\n".join(sorted(set(too_small)))


def test_dialogs_and_dark_theme(page):
    """Les fenêtres (menu, nouveau dossier) et le thème sombre respectent aussi WCAG 2.2 AA."""
    page.goto(page.root + "/#/fichiers?chemin=" + urllib.parse.quote("/Films et séries/Films"))
    page.locator(".btn-menu").first.click()
    expect(page.locator("dialog[open]")).to_be_visible()
    assert not scan(page), describe("menu •••", scan(page))
    page.keyboard.press("Escape")
    page.locator(".btn-menu").first.click()
    page.locator("dialog[open] button", has_text="Déplacer vers…").click()
    expect(page.locator("dialog[open] .ligne-dossier").first).to_be_visible()
    assert not scan(page), describe("sélecteur de dossier", scan(page))
    page.keyboard.press("Escape")
    page.click("#nouveau")
    expect(page.locator("dialog[open] input")).to_be_visible()
    assert not scan(page), describe("nouveau dossier", scan(page))
    page.keyboard.press("Escape")
    page.emulate_media(color_scheme="dark")
    problems = []
    for ecran in ["accueil", "outils", "fichiers", "systeme", "invites"]:
        page.goto(page.root + "/#/" + ecran)
        page.wait_for_timeout(600)
        violations = scan(page)
        if violations:
            problems.append(describe(ecran + " (sombre)", violations))
    assert not problems, "\n".join(problems)


def test_focus_is_not_hidden_under_the_bar(page):
    """WCAG 2.4.11 : le dernier élément de la liste, pris au clavier, reste au-dessus de la barre d'onglets."""
    if page.appareil != "iphone":
        pytest.skip("la barre d'onglets n'existe que sur téléphone")
    page.goto(page.root + "/#/systeme")
    page.wait_for_timeout(800)
    for selector in ["#contenu a, #contenu button"]:
        latest = page.locator(selector)
        target = latest.nth(latest.count() - 1)
        target.focus()
        page.wait_for_timeout(200)
        box = target.bounding_box()
        bar = page.locator(".onglets").bounding_box()
        assert box["y"] + box["height"] <= bar["y"] + 1, f"caché : {box} sous {bar}"
