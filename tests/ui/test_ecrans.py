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


def test_acces_par_le_domaine_dans_l_etabli(page):
    """On saisit son domaine, Jabba met Caddy en place et montre les étapes ; on peut refermer."""
    aller(page, "outils")
    carte = page.locator("#carte-domaine")
    expect(carte).to_contain_text("Jabba reste privé")
    carte.locator("#domaine-nom").fill("exemple.fr")
    carte.locator("#domaine-activer").click()
    expect(carte).to_contain_text("En ligne")
    expect(carte.locator("a")).to_have_attribute("href", "https://cloud.exemple.fr")
    expect(carte).to_contain_text("port externe 443, TCP, vers 192.168.1.20, port 8443")
    carte.locator("#domaine-retirer").click()
    page.locator("dialog[open]").get_by_role("button", name="Fermer l'accès").click()
    expect(carte.locator("#domaine-activer")).to_be_visible()


def test_outils_a_activer_et_memoire(page):
    """Les nouveaux outils sont dans la grille de l'Établi, interrupteur éteint, avec la mémoire qu'ils prendraient."""
    aller(page, "outils")
    expect(page.locator("#etabli-memoire")).to_contain_text("Mémoire du serveur")
    expect(page.locator("#etabli-memoire")).to_contain_text("Libre")
    barre = page.locator("#etabli-memoire .barre-memoire")
    expect(barre).to_have_attribute("role", "img")
    largeurs = [
        barre.locator(part).evaluate("e => parseFloat(e.style.width)") for part in (".part-outils", ".part-systeme")
    ]
    assert 0 < sum(largeurs) <= 100  # la barre se remplit selon la mémoire utilisée
    for outil in (
        "vaultwarden",
        "immich",
        "time-machine",
        "adguard-home",
        "paperless",
        "homebox",
        "ntfy",
        "changedetection",
    ):
        interrupteur = page.locator(f'#grille-etabli [data-activer="{outil}"]')
        expect(interrupteur).to_have_attribute("role", "switch")
        expect(interrupteur).to_have_attribute("aria-checked", "false")
    immich = page.locator('[data-offre="immich"]')
    expect(immich).to_contain_text("Prend environ 3,0 Go de mémoire")
    expect(page.locator('[data-offre="time-machine"] input[type="password"]')).to_be_visible()
    expect(page.locator('[data-offre="vaultwarden"] input')).to_have_count(0)
    expect(page.locator('[data-offre="paperless"]')).to_contain_text("Mot de passe du compte « admin » de Paperless")


def test_memoire_d_atelier(page):
    """Activée dans l'Établi : on ajoute une machine, on note une intervention, on fait un entretien."""
    import uuid

    machine = "Laser " + uuid.uuid4().hex[:6]  # la démo garde ses données d'un essai à l'autre
    aller(page, "outils")
    carte = page.locator('[data-integre="atelier"]')
    page.locator('[data-activer="atelier"], [data-integre="atelier"]').first.wait_for()
    if page.locator('[data-activer="atelier"]').count():
        page.locator('[data-activer="atelier"]').click()  # l'interrupteur, comme pour les autres outils
    expect(carte).to_be_visible()
    carte.get_by_role("link", name="Ouvrir").click()
    page.get_by_role("button", name="Ajouter une machine").click()
    page.locator("#form-machine input[name=nom]").fill(machine)
    page.locator("#form-machine input[name=modele]").fill("Speedy 360")
    page.locator("#form-machine").get_by_role("button", name="Ajouter").click()
    expect(page.locator("h1")).to_have_text(machine)
    page.locator("#form-entree input[name=titre]").fill("Lentille nettoyée")
    page.locator("#form-entree").get_by_role("button", name="Noter").click()
    expect(page.locator(".entree-atelier")).to_contain_text("Lentille nettoyée")
    page.locator("#form-rappel input[name=titre]").fill("Vider le bac à déchets")
    page.locator("#form-rappel").get_by_role("button", name="Ajouter le rappel").click()
    page.get_by_role("button", name="Fait aujourd'hui").click()
    expect(page.locator(".entree-atelier").first).to_contain_text("Vider le bac à déchets")
    page.get_by_role("link", name="← Toutes les machines").click()
    expect(page.locator(".machine-atelier", has_text=machine)).to_contain_text("Speedy 360")
    aller(page, "outils")  # désinstallée par le menu « ••• », elle garde tout ; rallumée, elle le retrouve
    page.locator('[data-menu="atelier"]').click()
    page.get_by_role("menuitem", name="Désinstaller").click()
    page.locator("dialog[open]").get_by_role("button", name="Désinstaller").click()
    expect(page.locator('[data-activer="atelier"]')).to_have_attribute("aria-checked", "false")
    page.locator('[data-activer="atelier"]').click()
    page.locator('[data-integre="atelier"]').get_by_role("link", name="Ouvrir").click()
    expect(page.locator(".machine-atelier", has_text=machine)).to_be_visible()


def test_menu_des_cartes_de_l_etabli(page):
    """« ••• » sur chaque carte : Aide et Informations s'ouvrent dans la carte ; Désinstaller seulement pour ce que Jabba a installé."""
    aller(page, "outils")
    jellyfin = page.locator("article.outil", has=page.locator('[data-menu="jellyfin"]'))
    jellyfin.locator('[data-menu="jellyfin"]').click()
    menu = jellyfin.get_by_role("menu")
    expect(menu).to_be_visible()
    expect(menu.get_by_role("menuitem")).to_have_text(["Aide", "Informations"])  # installé hors de Jabba
    menu.get_by_role("menuitem", name="Aide").click()
    expect(menu).to_be_hidden()
    expect(jellyfin.locator(".panneau-outil")).to_contain_text("films et séries")
    jellyfin.locator('[data-menu="jellyfin"]').click()
    jellyfin.get_by_role("menuitem", name="Informations").click()
    expect(jellyfin.locator(".panneau-outil")).to_contain_text("Conteneurs")
    page.keyboard.press("Escape")
    immich = page.locator('[data-offre="immich"]')
    immich.locator('[data-menu="immich"]').click()
    expect(immich.get_by_role("menuitem")).to_have_text(["Aide", "Informations"])  # pas encore installé
    page.keyboard.press("Escape")
    expect(immich.get_by_role("menu")).to_be_hidden()


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
