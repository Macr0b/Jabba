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
    expect(carte.get_by_role("link", name="https://cloud.exemple.fr")).to_have_attribute(
        "href", "https://cloud.exemple.fr"
    )
    expect(carte).to_contain_text("port externe 443, TCP, vers 192.168.1.20, port 8443")
    # DNS réglé par Jabba : un lien vers OVH, droits limités à la zone du domaine, et les trois codes à coller.
    lien = carte.get_by_role("link", name="Créer la clé chez OVH")
    expect(lien).to_have_attribute(
        "href", re.compile(r"^https://eu\.api\.ovh\.com/createToken/\?GET=%2Fdomain%2Fzone%2Fexemple\.fr%2F%2A")
    )
    expect(carte.locator("#ovh-as")).to_have_attribute("type", "password")
    carte.locator("#ovh-ak").fill("court")
    carte.get_by_role("button", name="Enregistrer et régler le DNS").click()
    expect(page.get_by_text("recopie-les en entier")).to_be_visible()  # refusé avant d'appeler OVH
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
        "paperless",
        "homebox",
        "changedetection",
    ):
        interrupteur = page.locator(f'#grille-etabli [data-activer="{outil}"]')
        expect(interrupteur).to_have_attribute("role", "switch")
        expect(interrupteur).to_have_attribute("aria-checked", "false")
    immich = page.locator('[data-offre="immich"]')
    expect(immich).to_contain_text("Prend environ 3,0 Go de mémoire")
    expect(page.locator('#grille-etabli [data-offre] input[type="password"]')).to_have_count(0)  # rien à taper


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
    identifiant = page.locator(".entree-atelier", has_text="Lentille nettoyée").get_attribute("data-entree")
    note = page.locator(f'.entree-atelier[data-entree="{identifiant}"]')
    note.get_by_role("button", name="Modifier").click()  # corrigée sur place, sans fenêtre par-dessus
    note.locator("input[name=titre]").fill("Lentille nettoyée à l'alcool")
    note.locator("input[name=pieces]").fill("Coton-tige")
    note.get_by_role("button", name="Enregistrer").click()
    corrigee = page.locator(".entree-atelier", has_text="Lentille nettoyée à l'alcool")
    expect(corrigee).to_contain_text("Coton-tige")
    expect(corrigee).to_contain_text("modifiée")
    note.get_by_role("button", name="Modifier").click()
    note.get_by_role("button", name="Annuler").click()
    expect(page.locator(".entree-atelier form")).to_have_count(0)
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
    expect(menu.get_by_role("menuitem")).to_have_text(
        ["Identifiants", "Installer sur cet appareil", "Aide", "Informations"]
    )  # installé hors de Jabba
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


def test_bloqueur_de_pubs_appareil_par_appareil(page):
    """AdGuard : menu « ••• » → Appareils ; un interrupteur par appareil, rien n'est filtré sans le choisir."""
    aller(page, "outils")
    carte = page.locator("article.outil", has=page.locator('[data-menu="adguard-home"]'))
    carte.locator('[data-menu="adguard-home"]').click()
    expect(carte.get_by_role("menuitem")).to_have_text(
        ["Appareils", "Identifiants", "Installer sur cet appareil", "Aide", "Informations", "Désinstaller"]
    )
    carte.get_by_role("menuitem", name="Appareils").click()
    panneau = carte.locator(".panneau-outil")
    expect(panneau).to_contain_text("Un nouvel appareil passe sans filtre")
    tele = panneau.get_by_role("switch", name="Bloqueur de pubs pour television")
    expect(tele).to_have_attribute("aria-checked", "false")
    expect(panneau.get_by_role("switch", name="Bloqueur de pubs pour macbook")).to_have_attribute(
        "aria-checked", "true"
    )
    expect(panneau).to_contain_text("Tailscale · 100.64.0.2")
    tele.click()
    expect(tele).to_have_attribute("aria-checked", "true")
    tele.click()  # et on le rééteint : la démo reste comme au départ pour les autres tests
    expect(tele).to_have_attribute("aria-checked", "false")


def test_identifiants_d_un_outil(page):
    """Le compte que Jabba a créé dans l'outil : identifiant et mot de passe, masqué puis affiché, à copier."""
    aller(page, "outils")
    carte = page.locator("article.outil", has=page.locator('[data-menu="adguard-home"]'))
    carte.locator('[data-menu="adguard-home"]').click()
    carte.get_by_role("menuitem", name="Identifiants").click()
    panneau = carte.locator(".panneau-outil")
    expect(panneau).to_contain_text("admin")
    expect(panneau).not_to_contain_text("demo-adguard-home")
    panneau.get_by_role("button", name="Afficher").click()
    expect(panneau).to_contain_text("demo-adguard-home")
    expect(panneau.get_by_role("button", name="Copier")).to_have_count(2)


def test_fenetre_ancienne_se_recharge_apres_mise_a_jour(page):
    """Une fenêtre ouverte avant une mise à jour se recharge seule, une fois, quand le serveur est plus récent."""
    aller(page, "outils")
    chargements = []
    page.on("load", lambda: chargements.append(1))

    def serveur_plus_recent(route):
        reponse = route.fetch()
        route.fulfill(response=reponse, headers={**reponse.headers, "x-jabba-version": "99.0.0"})

    page.route("**/api/**", serveur_plus_recent)
    aller(page, "systeme")
    page.wait_for_timeout(1500)
    assert len(chargements) == 1  # rechargée une fois, pas en boucle
    expect(page.locator("main")).to_be_visible()
    page.unroute("**/api/**")


def test_identifiants_notes_dans_jabba(page):
    """Un outil où l'on a créé son compte soi-même : on note l'identifiant et le mot de passe, Jabba les garde."""
    aller(page, "outils")
    carte = page.locator("article.outil", has=page.locator('[data-menu="dockge"]'))
    carte.locator('[data-menu="dockge"]').click()
    carte.get_by_role("menuitem", name="Identifiants").click()
    panneau = carte.locator(".panneau-outil")
    expect(panneau).to_contain_text("Jabba les garde pour toi")
    secret = panneau.locator('[data-champ="mot_de_passe"]')
    assert secret.get_attribute("type") == "text"  # pas un champ « mot de passe » : le trousseau ne s'en mêle pas
    panneau.locator('[data-champ="identifiant"]').fill("rob")
    secret.fill("mon secret")
    panneau.get_by_role("button", name="Enregistrer").click()
    expect(panneau).to_contain_text("Le compte que tu as noté pour Dockge")
    expect(panneau).not_to_contain_text("mon secret")
    panneau.get_by_role("button", name="Afficher").click()
    expect(panneau).to_contain_text("mon secret")
    panneau.get_by_role("button", name="Modifier").click()
    expect(panneau.locator('[data-champ="identifiant"]')).to_have_value("rob")
    panneau.get_by_role("button", name="Annuler").click()
    panneau.get_by_role("button", name="Oublier").click()
    expect(panneau.locator('[data-champ="identifiant"]')).to_have_value("")


def test_installer_un_outil_comme_appli(page):
    """« Installer sur cet appareil » : les clics à faire dans ce navigateur et un lien pour ouvrir l'outil."""
    aller(page, "outils")
    carte = page.locator("article.outil", has=page.locator('[data-menu="adguard-home"]'))
    carte.locator('[data-menu="adguard-home"]').click()
    carte.get_by_role("menuitem", name="Installer sur cet appareil").click()
    panneau = carte.locator(".panneau-outil")
    expect(panneau).to_contain_text("devient une appli à part")
    expect(panneau.locator("strong")).to_contain_text("Dans ")  # les clics propres au navigateur utilisé
    lien = panneau.get_by_role("link", name="Ouvrir AdGuard Home dans le navigateur")
    expect(lien).to_have_attribute("target", "_blank")
    expect(lien).to_have_attribute("href", "https://jabba.exemple.ts.net:3000")


def test_ranger_les_cartes_de_l_etabli(page):
    """« Ranger » : une carte descend avec ↓, « Terminé » enregistre, l'ordre revient au rechargement."""
    aller(page, "outils")
    grille = page.locator("#grille-etabli")
    expect(grille.locator("[data-offre]").first).to_be_visible()  # les cartes à activer sont arrivées
    premiere = grille.locator(":scope > article").first.evaluate(
        "e => e.dataset.carte || e.dataset.offre || e.dataset.integre"
    )
    page.get_by_role("button", name="Ranger").click()
    expect(grille).to_have_class(re.compile("rangement"))
    grille.locator(":scope > article").first.locator("[data-descendre]").click()
    deuxieme = (
        grille.locator(":scope > article")
        .nth(1)
        .evaluate("e => e.dataset.carte || e.dataset.offre || e.dataset.integre")
    )
    assert deuxieme == premiere
    page.get_by_role("button", name="Terminé").click()
    expect(page.get_by_role("button", name="Ranger")).to_be_visible()
    page.reload()
    expect(grille.locator("[data-offre]").first).to_be_visible()
    rang = (
        grille.locator(":scope > article")
        .nth(1)
        .evaluate("e => e.dataset.carte || e.dataset.offre || e.dataset.integre")
    )
    assert rang == premiere  # gardé sur le serveur
    # Remise en place pour les autres tests : la carte remonte.
    page.get_by_role("button", name="Ranger").click()
    grille.locator(":scope > article").nth(1).locator("[data-monter]").click()
    page.get_by_role("button", name="Terminé").click()
    expect(page.get_by_role("button", name="Ranger")).to_be_visible()


def test_masquer_une_carte_et_ranger_l_accueil(page):
    """« Masquer » cache la carte hors rangement ; « Afficher » la remet. L'Accueil se range aussi."""
    aller(page, "outils")
    grille = page.locator("#grille-etabli")
    carte = grille.locator('[data-offre="immich"]')
    expect(carte).to_be_visible()
    page.get_by_role("button", name="Ranger").click()
    carte.get_by_role("button", name="Masquer").click()
    page.get_by_role("button", name="Terminé").click()
    expect(carte).to_be_hidden()
    page.reload()
    expect(grille.locator("[data-offre]").first).to_be_visible()
    expect(grille.locator('[data-offre="immich"]')).to_be_hidden()  # gardé sur le serveur
    page.get_by_role("button", name="Ranger").click()
    grille.locator('[data-offre="immich"]').get_by_role("button", name="Afficher").click()
    page.get_by_role("button", name="Terminé").click()
    expect(grille.locator('[data-offre="immich"]')).to_be_visible()

    aller(page, "accueil")
    mesures = page.locator("#mesures-accueil")
    expect(mesures.locator(":scope > [data-carte]").first).to_have_attribute("data-carte", "processeur")
    page.get_by_role("button", name="Ranger").click()
    mesures.locator('[data-carte="processeur"]').get_by_role("button", name="Descendre Processeur").click()
    page.get_by_role("button", name="Terminé").click()
    page.reload()
    expect(mesures.locator(":scope > [data-carte]").nth(1)).to_have_attribute("data-carte", "processeur")
    page.get_by_role("button", name="Ranger").click()
    mesures.locator('[data-carte="processeur"]').get_by_role("button", name="Monter Processeur").click()
    page.get_by_role("button", name="Terminé").click()
    expect(mesures.locator(":scope > [data-carte]").first).to_have_attribute("data-carte", "processeur")


def test_adresses_des_outils(page):
    """Une adresse à part par outil : on colle une clé Tailscale, Jabba crée les adresses puis l'oublie.
    (En dernier : les liens des outils changent ensuite pour la suite de la démo.)"""
    aller(page, "outils")
    carte = page.locator("#carte-adresses")
    expect(carte).to_contain_text("AdGuard Home")
    if page.viewport_size["width"] < 700:  # même serveur de démo pour les deux écrans : déjà créées au 1er passage
        expect(carte).to_contain_text("adguard-home.exemple.ts.net")
        return
    expect(carte).to_contain_text("À créer")
    lien = carte.get_by_role("link", name="Ouvrir la page des clés Tailscale")
    expect(lien).to_have_attribute("href", "https://login.tailscale.com/admin/settings/keys")
    cle = carte.locator("#adresses-cle")
    assert cle.get_attribute("type") == "text"  # pas un champ « mot de passe » : le trousseau ne s'en mêle pas
    cle.fill("pas-une-cle")
    carte.get_by_role("button", name="Créer les adresses").click()
    expect(page.get_by_text("commence par « tskey-api- »")).to_be_visible()
    cle.fill("tskey-api-kDemo123456-AbCdEfGhIjKl")
    carte.get_by_role("button", name="Créer les adresses").click()
    expect(page.locator("#carte-adresses")).to_contain_text("adguard-home.exemple.ts.net", timeout=15000)
    expect(page.locator("#carte-adresses #adresses-cle")).to_have_count(0)  # tout est créé : plus rien à coller
    outil = page.locator("article.outil", has=page.locator('[data-menu="adguard-home"]'))
    expect(outil.get_by_role("link", name="Ouvrir")).to_have_attribute("href", "https://adguard-home.exemple.ts.net")


def test_mes_codes(page):
    """Système → Mes codes : trois groupes, ce que fait chaque code, jamais sa valeur."""
    aller(page, "systeme")
    carte = page.locator("#mes-codes")
    expect(carte).to_contain_text("À garder dans ton trousseau")
    expect(carte).to_contain_text("Gardés par Jabba")
    expect(carte).to_contain_text("Tu peux les supprimer de ton trousseau")
    expect(carte).to_contain_text("Mot de passe du serveur")
    expect(carte).to_contain_text("Compte AdGuard Home")
    expect(carte).not_to_contain_text("demo-adguard-home")  # le mot de passe de la démo n'apparaît jamais
