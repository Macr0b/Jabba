"""Les écrans principaux s'affichent et répondent, sur ordinateur et sur iPhone."""

import re
import urllib.parse

from outils_ui import attendre
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
    attendre(
        page,
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


ECRANS = [
    "accueil",
    "outils",
    "fichiers",
    "disques",
    "invites",
    "nuage",
    "systeme",
    "journal",
    "utilisateurs",
    "sauvegardes",
    "corbeille",
    "aide",
    "plus",
]


def test_chaque_ecran_s_ouvre_sans_erreur(page):
    """Chaque écran s'affiche (un titre) et aucune erreur JavaScript ne survient (vérifiée à la fin du test)."""
    for ecran in ECRANS:
        aller(page, ecran)
        page.wait_for_timeout(400)
        expect(page.locator("#contenu h1").first).to_be_visible()


def test_consommation_des_outils(page):
    aller(page, "outils")
    expect(page.locator('[data-conso="nextcloud"]')).to_have_text("Mémoire 610 Mo · processeur 2,4 %")


def test_entretien_des_disques(page):
    aller(page, "disques")
    carte = page.locator("#sante-disques")
    expect(carte).to_contain_text("SSD : TRIM chaque semaine")
    expect(carte).to_contain_text("R2-D2 : fragmentation faible")
    expect(carte.locator("[data-defragmenter]")).to_have_count(0)  # rien à défragmenter sous 31


def test_tuiles_des_disques_nommees(page):
    """Chaque tuile porte le nom de ce qu'elle contient ; le modèle en petit ; démarrage et swap repliés."""
    aller(page, "disques")
    titres = page.locator("article.disque h2")
    expect(titres).to_have_text(["Disque système", "Disque de données", "R2-D2", "PHOTOS 2019", "Time Machine"])
    systeme = page.locator("article.disque").first
    expect(systeme).to_contain_text("Disque interne")
    expect(systeme).to_contain_text("SSD 128 Go")
    technique = systeme.locator("details.parts-techniques")
    expect(technique).to_contain_text("Partitions techniques (1)")
    expect(technique.locator(".part")).to_be_hidden()  # replié par défaut


def test_antivirus_propose_dans_l_etabli(page):
    aller(page, "outils")
    offre = page.locator("#offre-antivirus")
    expect(offre).to_contain_text("Antivirus")
    expect(offre).to_contain_text("mis à l'écart (jamais supprimé)")
    expect(offre.locator("#installer-antivirus")).to_have_text("Installer")


NUAGE_PRET = {
    "etat": {"present": True, "pret": True, "quota_defaut": "5 GB"},
    "rang": "super",
    "plafond_go": 50,
    "comptes": [],
    "groupes": [],
    "rangs_actif": False,
    "rangs": [],
    "createur": True,
    "charte": "",
    "charte_defaut": "",
    "charte_max": 1500,
    "dossiers": [],
    "modes": {"lecture": {"nom": "Lecture", "detail": ""}},
    "tous": "membres",
    "promis": 0,
    "espace": None,
}


def test_dossiers_partages_se_chargent(page):
    """La liste « Dossiers partagés » restait sur « Chargement… » (jamais demandée) : corrigé en 0.14."""
    import json

    page.route("**/api/nuage", lambda route: route.fulfill(json=NUAGE_PRET))
    partage = {
        "partages": [{"id": "p1", "nom": "Plans", "chemin": "/Plans", "groupe": "membres", "mode": "lecture"}],
        "modes": {"lecture": "Lecture"},
    }
    page.route(
        "**/api/partages", lambda route: route.fulfill(body=json.dumps(partage), content_type="application/json")
    )
    aller(page, "nuage")
    page.locator('[data-onglet-btn="partage"]').click()
    expect(page.locator("#partages-liste")).not_to_contain_text("Chargement…")
