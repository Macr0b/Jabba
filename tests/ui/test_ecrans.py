"""Les écrans principaux s'affichent et répondent, sur ordinateur et sur iPhone."""

import re
import urllib.parse

from playwright.sync_api import expect


def aller(page, chemin):
    page.goto(page.racine + "/#/" + chemin)
    page.wait_for_timeout(300)


def test_accueil(page):
    aller(page, "")
    expect(page.locator("#contenu h1").first).to_be_visible()


def test_fichiers(page):
    aller(page, "fichiers?chemin=" + urllib.parse.quote("/Films et séries/Films"))
    expect(page.get_by_text("Film test.mkv")).to_be_visible()


def test_lecteur(page):
    """Le lecteur : vidéo convertie par le serveur, commandes par-dessus l'image, menu, plein écran, ±10 s."""
    aller(page, "fichiers?chemin=" + urllib.parse.quote("/Films et séries/Films"))
    page.get_by_text("Film test.mkv").click()
    page.wait_for_function(
        "(() => { const v = document.querySelector('#lecteur video'); return v && v.readyState >= 3 && v.currentTime > 0.3; })()",
        timeout=30000,
    )
    commandes = page.locator("#lecteur .commandes")
    video = page.locator("#lecteur video").bounding_box()
    boite = commandes.bounding_box()
    assert boite["y"] + boite["height"] <= video["y"] + video["height"] + 1, "les commandes doivent être sur l'image"
    plein = page.locator("#lect-plein").bounding_box()
    assert plein["x"] > video["x"] + video["width"] * 0.8, "le plein écran doit être en bas à droite"
    page.click("#lect-reglages")
    expect(page.locator("#lect-menu")).to_be_visible()
    page.click("#lect-reglages")
    expect(page.locator("#lect-menu")).to_be_hidden()
    page.click("#lect-plein")
    page.wait_for_timeout(400)
    assert page.evaluate(
        "!!document.fullscreenElement || document.querySelector('#lecteur').classList.contains('plein')"
    )
    page.click("#lect-plein")
    page.wait_for_timeout(400)
    if page.appareil == "ordinateur":
        avant = page.evaluate("+document.querySelector('#lect-barre').value")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(800)
        assert page.evaluate("+document.querySelector('#lect-barre').value") >= avant + 8
    page.wait_for_timeout(3500)
    assert page.evaluate("document.querySelector('#lecteur').classList.contains('repos')"), (
        "les commandes doivent se cacher pendant la lecture"
    )


def test_systeme(page):
    aller(page, "systeme")
    expect(page.locator("#redem h2")).to_have_text("Redémarrage après une mise à jour")
    expect(page.locator("#redem .pastille")).to_be_visible()
    expect(page.locator("#maj h2")).to_have_text("Appli Jabba")


def test_terminal(page):
    aller(page, "terminal")
    page.wait_for_function("document.querySelector('#terminal .xterm')", timeout=10000)
    assert page.evaluate("document.querySelector('#terminal textarea').getAttribute('autocomplete')") == "off"
    if page.appareil == "ordinateur":
        return  # sur ordinateur, copier-coller passe par le clavier (barre de touches réservée aux écrans tactiles)
    page.evaluate("navigator.clipboard.writeText('echo jabba-ui-ok')")
    page.click("#t-coller")
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    page.click("#t-copier")
    page.wait_for_timeout(300)
    assert "jabba-ui-ok" in page.evaluate("navigator.clipboard.readText()")


def test_sauvegardes(page):
    aller(page, "sauvegardes")
    expect(page.locator("#contenu h1").first).to_have_text("Sauvegardes")


def test_comptes_nextcloud_sans_nextcloud(page):
    aller(page, "nuage")
    expect(page.get_by_text("Nextcloud n'est pas allumé")).to_be_visible()


def test_comptes_dans_le_menu(page):
    """Les comptes ont leur entrée dans le menu de gauche (ordinateur) et dans Plus (iPhone)."""
    aller(page, "")
    if page.appareil == "ordinateur":
        page.locator('.barre-laterale a[data-page="comptes"]').click()
        expect(page.locator('.barre-laterale a[data-page="comptes"]')).to_have_attribute("aria-current", "page")
    else:
        aller(page, "plus")
        page.get_by_text("Comptes Nextcloud").click()
    expect(page).to_have_url(re.compile(r"#/nuage$"))
    aller(page, "utilisateurs")
    expect(page.locator("#contenu h1").first).to_have_text("Admins de Jabba")


def test_etabli(page):
    aller(page, "outils")
    expect(page.locator("#contenu h1").first).to_have_text("Établi")


ECRANS = ["accueil", "outils", "fichiers", "disques", "invites", "nuage", "systeme", "journal", "utilisateurs",
          "sauvegardes", "corbeille", "aide", "plus"]


def test_chaque_ecran_s_ouvre_sans_erreur(page):
    """Chaque écran s'affiche (un titre) et aucune erreur JavaScript ne survient (vérifiée à la fin du test)."""
    for ecran in ECRANS:
        aller(page, ecran)
        page.wait_for_timeout(400)
        expect(page.locator("#contenu h1").first).to_be_visible()
