"""Apparence : clair, sombre, ou comme l'appareil ; le choix tient après rechargement, sans flash."""

from playwright.sync_api import expect

FOND = "getComputedStyle(document.body).backgroundColor"
CLAIR = "rgb(238, 244, 242)"
SOMBRE = "rgb(11, 21, 20)"


def choisir(page, theme):
    page.locator(f'#apparence input[value="{theme}"]').check()


def test_choix_du_theme(page):
    page.emulate_media(color_scheme="light")
    page.goto(page.racine + "/#/systeme")
    expect(page.locator("#apparence h2")).to_have_text("Apparence")
    expect(page.locator('#apparence input[value="auto"]')).to_be_checked()
    assert page.evaluate(FOND) == CLAIR

    choisir(page, "sombre")
    assert page.evaluate(FOND) == SOMBRE
    assert page.evaluate("document.querySelector('meta[name=theme-color]').content") == "#0B1514"
    page.reload()
    expect(page.locator('#apparence input[value="sombre"]')).to_be_checked()
    assert page.evaluate("document.documentElement.dataset.theme") == "sombre"  # posé avant l'affichage
    assert page.evaluate(FOND) == SOMBRE

    page.emulate_media(color_scheme="dark")
    choisir(page, "clair")
    assert page.evaluate(FOND) == CLAIR  # l'appareil est en sombre, mais on a choisi clair

    choisir(page, "auto")
    assert page.evaluate(FOND) == SOMBRE  # retour au réglage de l'appareil
    assert page.evaluate("localStorage.getItem('jabba-theme')") is None
    page.emulate_media(color_scheme="light")
    assert page.evaluate(FOND) == CLAIR


def test_choix_du_style(page):
    """Trois styles : Cube (de base), Salle des machines, Atelier clair ; gardé sur l'appareil, posé avant l'affichage."""
    page.emulate_media(color_scheme="dark")
    page.goto(page.racine + "/#/systeme")
    expect(page.locator('#apparence input[name="style"][value="cube"]')).to_be_checked()
    assert page.evaluate("document.documentElement.dataset.style") is None

    page.locator('#apparence input[name="style"][value="machines"]').check()
    assert page.evaluate("document.documentElement.dataset.style") == "machines"
    assert page.evaluate(FOND) == "rgb(23, 20, 15)"
    assert page.evaluate("document.querySelector('meta[name=theme-color][media*=dark]').content") == "#17140F"
    page.reload()
    expect(page.locator('#apparence input[name="style"][value="machines"]')).to_be_checked()
    assert page.evaluate("document.documentElement.dataset.style") == "machines"
    assert "Space Grotesk" in page.evaluate("getComputedStyle(document.body).fontFamily")

    page.locator('#apparence input[name="style"][value="atelier"]').check()
    choisir(page, "clair")
    assert page.evaluate(FOND) == "rgb(243, 241, 236)"  # le style et le thème se combinent

    page.locator('#apparence input[name="style"][value="cube"]').check()
    assert page.evaluate("localStorage.getItem('jabba-style')") is None
    assert page.evaluate(FOND) == CLAIR
    choisir(page, "auto")
