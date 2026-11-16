"""Apparence : clair, sombre, ou comme l'appareil ; le choix tient après rechargement, sans flash."""

from playwright.sync_api import expect

FOND = "getComputedStyle(document.body).backgroundColor"
CLAIR = "rgb(244, 245, 247)"
SOMBRE = "rgb(18, 20, 24)"


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
    assert page.evaluate("document.querySelector('meta[name=theme-color]').content") == "#121418"
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
