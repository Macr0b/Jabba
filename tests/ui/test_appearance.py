"""Apparence : clair, sombre, ou comme l'appareil ; le choix tient après rechargement, sans flash."""

from playwright.sync_api import expect

BACKGROUND = "getComputedStyle(document.body).backgroundColor"
LIGHT = "rgb(238, 244, 242)"
DARK = "rgb(11, 21, 20)"


def choose(page, theme):
    page.locator(f'#apparence input[value="{theme}"]').check()


def test_theme_choice(page):
    page.emulate_media(color_scheme="light")
    page.goto(page.root + "/#/systeme")
    expect(page.locator("#apparence h2")).to_have_text("Apparence")
    expect(page.locator('#apparence input[value="auto"]')).to_be_checked()
    assert page.evaluate(BACKGROUND) == LIGHT

    choose(page, "sombre")
    assert page.evaluate(BACKGROUND) == DARK
    assert page.evaluate("document.querySelector('meta[name=theme-color]').content") == "#0B1514"
    page.reload()
    expect(page.locator('#apparence input[value="sombre"]')).to_be_checked()
    assert page.evaluate("document.documentElement.dataset.theme") == "sombre"  # posé avant l'affichage
    assert page.evaluate(BACKGROUND) == DARK

    page.emulate_media(color_scheme="dark")
    choose(page, "clair")
    assert page.evaluate(BACKGROUND) == LIGHT  # l'appareil est en sombre, mais on a choisi clair

    choose(page, "auto")
    assert page.evaluate(BACKGROUND) == DARK  # retour au réglage de l'appareil
    assert page.evaluate("localStorage.getItem('jabba-theme')") is None
    page.emulate_media(color_scheme="light")
    assert page.evaluate(BACKGROUND) == LIGHT


def test_style_choice(page):
    """Trois styles : Cube (de base), Salle des machines, Atelier clair ; gardé sur l'appareil, posé avant l'affichage."""
    page.emulate_media(color_scheme="dark")
    page.goto(page.root + "/#/systeme")
    expect(page.locator('#apparence input[name="style"][value="cube"]')).to_be_checked()
    assert page.evaluate("document.documentElement.dataset.style") is None

    page.locator('#apparence input[name="style"][value="machines"]').check()
    assert page.evaluate("document.documentElement.dataset.style") == "machines"
    assert page.evaluate(BACKGROUND) == "rgb(23, 20, 15)"
    assert page.evaluate("document.querySelector('meta[name=theme-color][media*=dark]').content") == "#17140F"
    page.reload()
    expect(page.locator('#apparence input[name="style"][value="machines"]')).to_be_checked()
    assert page.evaluate("document.documentElement.dataset.style") == "machines"
    assert "Space Grotesk" in page.evaluate("getComputedStyle(document.body).fontFamily")

    page.locator('#apparence input[name="style"][value="atelier"]').check()
    choose(page, "clair")
    assert page.evaluate(BACKGROUND) == "rgb(243, 241, 236)"  # le style et le thème se combinent

    page.locator('#apparence input[name="style"][value="cube"]').check()
    assert page.evaluate("localStorage.getItem('jabba-style')") is None
    assert page.evaluate(BACKGROUND) == LIGHT
    choose(page, "auto")
