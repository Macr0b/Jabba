"""Les écrans principaux s'affichent et répondent, sur ordinateur et sur iPhone."""

import re
import urllib.parse

from playwright.sync_api import expect
from ui_tools import wait_until


def go_to(page, chemin):
    page.goto(page.root + "/#/" + chemin)
    page.wait_for_timeout(300)


def test_home(page):
    go_to(page, "")
    expect(page.locator("#contenu h1").first).to_be_visible()


def test_files(page):
    go_to(page, "fichiers?chemin=" + urllib.parse.quote("/Films et séries/Films"))
    expect(page.get_by_text("Film test.mkv")).to_be_visible()


def test_player(page):
    """Le lecteur : vidéo convertie par le serveur, commandes par-dessus l'image, menu, plein écran, ±10 s."""
    go_to(page, "fichiers?chemin=" + urllib.parse.quote("/Films et séries/Films"))
    page.get_by_text("Film test.mkv").click()
    wait_until(
        page,
        "(() => { const v = document.querySelector('#lecteur video'); return v && v.readyState >= 3 && v.currentTime > 0.3; })()",
        timeout=30000,
    )
    commands_seen = page.locator("#lecteur .commandes")
    video = page.locator("#lecteur video").bounding_box()
    box = commands_seen.bounding_box()
    assert box["y"] + box["height"] <= video["y"] + video["height"] + 1, "les commandes doivent être sur l'image"
    full_disk = page.locator("#lect-plein").bounding_box()
    assert full_disk["x"] > video["x"] + video["width"] * 0.8, "le plein écran doit être en bas à droite"
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
        before = page.evaluate("+document.querySelector('#lect-barre').value")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(800)
        assert page.evaluate("+document.querySelector('#lect-barre').value") >= before + 8
    page.wait_for_timeout(3500)
    assert page.evaluate("document.querySelector('#lecteur').classList.contains('repos')"), (
        "les commandes doivent se cacher pendant la lecture"
    )


def test_system(page):
    go_to(page, "systeme")
    expect(page.locator("#maj h2")).to_have_text("Appli Jabba")
    expect(page.locator("#redem")).to_have_count(0)  # les réglages sont dans Réglages


def test_settings_sections(page):
    """Réglages : une rubrique à la fois, gardée dans l'adresse ; chaque carte à sa place."""
    go_to(page, "reglages?r=serveur")
    expect(page.locator("#redem h2")).to_have_text("Redémarrage après une mise à jour")
    expect(page.locator("#redem .pastille")).to_be_visible()
    expect(page.locator("#apparence")).to_be_hidden()
    expect(page.locator("#outils-serveur .outil").first).to_be_visible()
    expect(page.locator("#outils-serveur [role=switch]")).to_have_count(0)  # pas de curseur sur le système
    page.get_by_role("tab", name="Moi").click()
    expect(page.locator("#apparence")).to_be_visible()
    expect(page).to_have_url(page.root + "/#/reglages?r=moi")
    page.get_by_role("tab", name="Internet").click()
    expect(page.locator("#carte-domaine")).to_be_visible()


def test_backups(page):
    go_to(page, "sauvegardes")
    expect(page.locator("#contenu h1").first).to_have_text("Sauvegardes")


def test_nextcloud_accounts_without_nextcloud(page):
    go_to(page, "nuage")
    expect(page.get_by_text("Nextcloud n'est pas allumé")).to_be_visible()


def test_accounts_in_the_menu(page):
    """Les comptes ont leur entrée dans le menu de gauche (ordinateur) et dans Plus (iPhone)."""
    go_to(page, "")
    if page.appareil == "ordinateur":
        page.locator('.barre-laterale a[data-page="comptes"]').click()
        expect(page.locator('.barre-laterale a[data-page="comptes"]')).to_have_attribute("aria-current", "page")
    else:
        go_to(page, "plus")
        page.get_by_text("Comptes Nextcloud").click()
    expect(page).to_have_url(re.compile(r"#/nuage$"))
    go_to(page, "utilisateurs")
    expect(page.locator("#contenu h1").first).to_have_text("Admins de Jabba")


def test_workbench(page):
    go_to(page, "outils")
    expect(page.locator("#contenu h1").first).to_have_text("Établi")


SCREENS_ = [
    "accueil",
    "outils",
    "fichiers",
    "disques",
    "invites",
    "nuage",
    "systeme",
    "reglages",
    "journal",
    "utilisateurs",
    "sauvegardes",
    "corbeille",
    "aide",
    "plus",
]


def test_every_screen_opens_without_error(page):
    """Chaque écran s'affiche (un titre) et aucune erreur JavaScript ne survient (vérifiée à la fin du test)."""
    for ecran in SCREENS_:
        go_to(page, ecran)
        page.wait_for_timeout(400)
        expect(page.locator("#contenu h1").first).to_be_visible()


def test_tool_usage(page):
    go_to(page, "outils")
    expect(page.locator('[data-conso="nextcloud"]')).to_have_text("Mémoire 610 Mo · processeur 2,4 %")


def test_disk_maintenance(page):
    go_to(page, "disques")
    card = page.locator("#sante-disques")
    expect(card).to_contain_text("SSD : TRIM chaque semaine")
    expect(card).to_contain_text("R2-D2 : fragmentation faible")
    expect(card.locator("[data-defragmenter]")).to_have_count(0)  # rien à défragmenter sous 31


def test_disk_tiles_named(page):
    """Chaque tuile porte le nom de ce qu'elle contient ; le modèle en petit ; démarrage et swap repliés."""
    go_to(page, "disques")
    titles = page.locator("article.disque h2")
    expect(titles).to_have_text(["Disque système", "Disque de données", "R2-D2", "PHOTOS 2019", "Time Machine"])
    system = page.locator("article.disque").first
    expect(system).to_contain_text("Disque interne")
    expect(system).to_contain_text("SSD 128 Go")
    technical = system.locator("details.parts-techniques")
    expect(technical).to_contain_text("Partitions techniques (1)")
    expect(technical.locator(".part")).to_be_hidden()  # replié par défaut


def test_antivirus_offered_in_the_workbench(page):
    go_to(page, "outils")
    offer = page.locator("#offre-antivirus")
    expect(offer).to_contain_text("Antivirus")
    expect(offer).to_contain_text("mis à l'écart (jamais supprimé)")
    expect(offer.locator("#installer-antivirus")).to_have_text("Installer")


def test_domain_access_in_settings(page):
    """On saisit son domaine, Jabba met Caddy en place et montre les étapes ; on peut refermer."""
    go_to(page, "reglages?r=internet")
    card = page.locator("#carte-domaine")
    expect(card).to_contain_text("Jabba reste privé")
    card.locator("#domaine-nom").fill("exemple.fr")
    card.locator("#domaine-activer").click()
    expect(card).to_contain_text("En ligne")
    expect(card.get_by_role("link", name="https://cloud.exemple.fr")).to_have_attribute(
        "href", "https://cloud.exemple.fr"
    )
    expect(card).to_contain_text("port externe 443, TCP, vers 192.168.1.20, port 8443")
    # DNS réglé par Jabba : Cloudflare d'abord (un jeton limité au domaine), OVH replié en dessous.
    expect(card.get_by_role("link", name="Créer le jeton chez Cloudflare")).to_have_attribute(
        "href", "https://dash.cloudflare.com/profile/api-tokens"
    )
    expect(card).to_contain_text("Zone spécifique → exemple.fr")
    expect(card.locator("#cf-jeton")).to_have_attribute("type", "password")
    card.locator("#cf-jeton").fill("court")
    card.locator("#cf-enregistrer").click()
    expect(page.get_by_text("recopie-le en entier")).to_be_visible()  # refusé avant d'appeler Cloudflare
    card.get_by_text("Domaine chez OVH ?").click()
    link = card.get_by_role("link", name="Créer la clé chez OVH")
    expect(link).to_have_attribute(
        "href", re.compile(r"^https://eu\.api\.ovh\.com/createToken/\?GET=%2Fdomain%2Fzone%2Fexemple\.fr%2F%2A")
    )
    expect(card.locator("#ovh-as")).to_have_attribute("type", "password")
    card.locator("#ovh-ak").fill("court")
    card.locator("#ovh-enregistrer").click()
    expect(page.get_by_text("recopie-les en entier")).to_be_visible()  # refusé avant d'appeler OVH
    card.locator("#domaine-retirer").click()
    page.locator("dialog[open]").get_by_role("button", name="Fermer l'accès").click()
    expect(card.locator("#domaine-activer")).to_be_visible()


def test_tools_to_enable_and_memory(page):
    """« Ajouter un outil » : rangés par usage, un bouton « Installer » et la mémoire qu'ils prendraient."""
    go_to(page, "outils")
    expect(page.locator("#etabli-memoire")).to_contain_text("Mémoire du serveur")
    expect(page.locator("#etabli-memoire")).to_contain_text("Libre")
    bar = page.locator("#etabli-memoire .barre-memoire")
    expect(bar).to_have_attribute("role", "img")
    widths = [
        bar.locator(part).evaluate("e => parseFloat(e.style.width)") for part in (".part-outils", ".part-systeme")
    ]
    assert 0 < sum(widths) <= 100  # la barre se remplit selon la mémoire utilisée
    for outil in (
        "vaultwarden",
        "immich",
        "time-machine",
        "paperless",
        "homebox",
        "changedetection",
    ):
        expect(page.locator(f'#ajouter-outils [data-activer="{outil}"]')).to_have_text("Installer")
    expect(page.locator("#ajouter-outils [role=switch]")).to_have_count(0)  # installer n'est pas un curseur
    group_list = page.locator("#ajouter-outils .titre-groupe")
    expect(group_list).to_have_text(["Fichiers et souvenirs", "Atelier et maison", "Sécurité", "Sauvegarde", "Veille"])
    expect(page.locator('#ajouter-outils [data-activer="nextcloud"]')).to_have_count(0)  # déjà installé (démo)
    immich = page.locator('[data-offre="immich"]')
    expect(immich).to_contain_text("Prend environ 3,0 Go de mémoire")
    expect(page.locator("#ajouter-outils #offre-antivirus")).to_be_visible()  # rangé dans « Sécurité »
    expect(page.locator('#ajouter-outils input[type="password"]')).to_have_count(0)  # rien à taper
    # Mes outils : le curseur met en pause, et dit ce qu'il fait.
    jellyfin = page.locator('#grille-etabli [data-bascule="jellyfin"]')
    expect(jellyfin).to_have_attribute("aria-label", "Mettre Jellyfin en pause")
    expect(page.locator('#grille-etabli [data-carte="jellyfin"]')).to_contain_text("En marche")
    expect(page.get_by_role("link", name="Réglages").last).to_have_attribute("href", "#/reglages?r=serveur")


def test_workshop_log(page):
    """Activée dans l'Établi : on ajoute une machine, on note une intervention, on fait un entretien."""
    import uuid

    machine = "Laser " + uuid.uuid4().hex[:6]  # la démo garde ses données d'un essai à l'autre
    go_to(page, "outils")
    card = page.locator('[data-carte="atelier"]')
    page.locator('[data-activer="atelier"], [data-carte="atelier"]').first.wait_for()
    if page.locator('[data-activer="atelier"]').count():
        page.locator('[data-activer="atelier"]').click()  # « Ajouter », dans « Ajouter un outil »
    expect(card).to_be_visible()
    card.get_by_role("link", name="Ouvrir").click()
    page.get_by_role("button", name="Ajouter une machine").click()
    page.locator("#form-machine input[name=name]").fill(machine)
    page.locator("#form-machine input[name=model]").fill("Speedy 360")
    page.locator("#form-machine").get_by_role("button", name="Ajouter").click()
    expect(page.locator("h1")).to_have_text(machine)
    page.locator("#form-entree input[name=title]").fill("Lentille nettoyée")
    page.locator("#form-entree").get_by_role("button", name="Noter").click()
    expect(page.locator(".entree-atelier")).to_contain_text("Lentille nettoyée")
    identifiant = page.locator(".entree-atelier", has_text="Lentille nettoyée").get_attribute("data-entree")
    note = page.locator(f'.entree-atelier[data-entree="{identifiant}"]')
    note.get_by_role("button", name="Modifier").click()  # corrigée sur place, sans fenêtre par-dessus
    note.locator("input[name=title]").fill("Lentille nettoyée à l'alcool")
    note.locator("input[name=parts]").fill("Coton-tige")
    note.get_by_role("button", name="Enregistrer").click()
    fixed_entry = page.locator(".entree-atelier", has_text="Lentille nettoyée à l'alcool")
    expect(fixed_entry).to_contain_text("Coton-tige")
    expect(fixed_entry).to_contain_text("modifiée")
    note.get_by_role("button", name="Modifier").click()
    note.get_by_role("button", name="Annuler").click()
    expect(page.locator(".entree-atelier form")).to_have_count(0)
    page.locator("#form-rappel input[name=title]").fill("Vider le bac à déchets")
    page.locator("#form-rappel").get_by_role("button", name="Ajouter le rappel").click()
    page.get_by_role("button", name="Fait aujourd'hui").click()
    expect(page.locator(".entree-atelier").first).to_contain_text("Vider le bac à déchets")
    page.get_by_role("link", name="← Toutes les machines").click()
    expect(page.locator(".machine-atelier", has_text=machine)).to_contain_text("Speedy 360")
    go_to(page, "outils")  # désinstallée par le menu « ••• », elle garde tout ; rallumée, elle le retrouve
    page.locator('[data-menu="atelier"]').click()
    page.get_by_role("menuitem", name="Désinstaller").click()
    page.locator("dialog[open]").get_by_role("button", name="Désinstaller").click()
    expect(page.locator('[data-activer="atelier"]')).to_have_text("Ajouter")
    page.locator('[data-activer="atelier"]').click()
    page.locator('[data-carte="atelier"]').get_by_role("link", name="Ouvrir").click()
    expect(page.locator(".machine-atelier", has_text=machine)).to_be_visible()


def test_workbench_card_menus(page):
    """« ••• » sur chaque carte : Aide et Informations s'ouvrent dans la carte ; Désinstaller seulement pour ce que Jabba a installé."""
    go_to(page, "outils")
    jellyfin = page.locator("article.outil", has=page.locator('[data-menu="jellyfin"]'))
    jellyfin.locator('[data-menu="jellyfin"]').click()
    menu = jellyfin.get_by_role("menu")
    expect(menu).to_be_visible()
    expect(menu.get_by_role("menuitem")).to_have_text(
        ["Identifiants", "Installer sur cet appareil", "Redémarrer", "Mettre à jour", "Aide", "Informations"]
    )  # installé hors de Jabba : pas de « Désinstaller »
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


CLOUD_READY = {
    "state": {"present": True, "ready": True, "default_quota": "5 GB"},
    "rank": "super",
    "cap_gb": 50,
    "accounts": [],
    "groups": [],
    "ranks_enabled": False,
    "ranks": [],
    "creator": True,
    "charter": "",
    "default_charter": "",
    "charter_max": 1500,
    "folders": [],
    "modes": {"lecture": {"name": "Lecture", "detail": ""}},
    "all": "membres",
    "promised": 0,
    "space": None,
}


def test_shared_folders_load(page):
    """La liste « Dossiers partagés » restait sur « Chargement… » (jamais demandée) : corrigé en 0.14."""
    import json

    page.route("**/api/nuage", lambda route: route.fulfill(json=CLOUD_READY))
    share = {
        "shares": [{"id": "p1", "name": "Plans", "path": "/Plans", "group": "membres", "mode": "lecture"}],
        "modes": {"lecture": "Lecture"},
    }
    page.route("**/api/partages", lambda route: route.fulfill(body=json.dumps(share), content_type="application/json"))
    go_to(page, "nuage")
    page.locator('[data-onglet-btn="partage"]').click()
    expect(page.locator("#partages-liste")).not_to_contain_text("Chargement…")


def test_ad_blocker_device_by_device(page):
    """AdGuard : menu « ••• » → Appareils ; un interrupteur par appareil, rien n'est filtré sans le choisir."""
    go_to(page, "outils")
    card = page.locator("article.outil", has=page.locator('[data-menu="adguard-home"]'))
    card.locator('[data-menu="adguard-home"]').click()
    expect(card.get_by_role("menuitem")).to_have_text(
        [
            "Appareils",
            "Identifiants",
            "Installer sur cet appareil",
            "Redémarrer",
            "Mettre à jour",
            "Aide",
            "Informations",
            "Désinstaller",
        ]
    )
    card.get_by_role("menuitem", name="Appareils").click()
    panel = card.locator(".panneau-outil")
    expect(panel).to_contain_text("Un nouvel appareil passe sans filtre")
    tv = panel.get_by_role("switch", name="Bloqueur de pubs pour television")
    expect(tv).to_have_attribute("aria-checked", "false")
    expect(panel.get_by_role("switch", name="Bloqueur de pubs pour macbook")).to_have_attribute("aria-checked", "true")
    expect(panel).to_contain_text("Tailscale · 100.64.0.2")
    tv.click()
    expect(tv).to_have_attribute("aria-checked", "true")
    tv.click()  # et on le rééteint : la démo reste comme au départ pour les autres tests
    expect(tv).to_have_attribute("aria-checked", "false")


def test_tool_logins(page):
    """Le compte que Jabba a créé dans l'outil : identifiant et mot de passe, masqué puis affiché, à copier."""
    go_to(page, "outils")
    card = page.locator("article.outil", has=page.locator('[data-menu="adguard-home"]'))
    card.locator('[data-menu="adguard-home"]').click()
    card.get_by_role("menuitem", name="Identifiants").click()
    panel = card.locator(".panneau-outil")
    expect(panel).to_contain_text("admin")
    expect(panel).not_to_contain_text("demo-adguard-home")
    panel.get_by_role("button", name="Afficher").click()
    expect(panel).to_contain_text("demo-adguard-home")
    expect(panel.get_by_role("button", name="Copier")).to_have_count(2)


def test_old_window_reloads_after_an_update(page):
    """Une fenêtre ouverte avant une mise à jour se recharge seule, une fois, quand le serveur est plus récent."""
    go_to(page, "outils")
    loads = []
    page.on("load", lambda: loads.append(1))

    def newer_server(route):
        answer = route.fetch()
        route.fulfill(response=answer, headers={**answer.headers, "x-jabba-version": "99.0.0"})

    page.route("**/api/**", newer_server)
    go_to(page, "systeme")
    page.wait_for_timeout(1500)
    assert len(loads) == 1  # rechargée une fois, pas en boucle
    expect(page.locator("main")).to_be_visible()
    page.unroute("**/api/**")


def test_logins_noted_in_jabba(page):
    """Un outil où l'on a créé son compte soi-même : on note l'identifiant et le mot de passe, Jabba les garde."""
    go_to(page, "outils")
    card = page.locator("article.outil", has=page.locator('[data-menu="dockge"]'))
    card.locator('[data-menu="dockge"]').click()
    card.get_by_role("menuitem", name="Identifiants").click()
    panel = card.locator(".panneau-outil")
    expect(panel).to_contain_text("Jabba les garde pour toi")
    secret = panel.locator('[data-champ="mot_de_passe"]')
    assert secret.get_attribute("type") == "text"  # pas un champ « mot de passe » : le trousseau ne s'en mêle pas
    panel.locator('[data-champ="identifiant"]').fill("rob")
    secret.fill("mon secret")
    panel.get_by_role("button", name="Enregistrer").click()
    expect(panel).to_contain_text("Le compte que tu as noté pour Dockge")
    expect(panel).not_to_contain_text("mon secret")
    panel.get_by_role("button", name="Afficher").click()
    expect(panel).to_contain_text("mon secret")
    panel.get_by_role("button", name="Modifier").click()
    expect(panel.locator('[data-champ="identifiant"]')).to_have_value("rob")
    panel.get_by_role("button", name="Annuler").click()
    panel.get_by_role("button", name="Oublier").click()
    expect(panel.locator('[data-champ="identifiant"]')).to_have_value("")


def test_install_a_tool_as_an_app(page):
    """« Installer sur cet appareil » : les clics à faire dans ce navigateur et un lien pour ouvrir l'outil."""
    go_to(page, "outils")
    card = page.locator("article.outil", has=page.locator('[data-menu="adguard-home"]'))
    card.locator('[data-menu="adguard-home"]').click()
    card.get_by_role("menuitem", name="Installer sur cet appareil").click()
    panel = card.locator(".panneau-outil")
    expect(panel).to_contain_text("devient une appli à part")
    expect(panel.locator("strong")).to_contain_text("Dans ")  # les clics propres au navigateur utilisé
    link = panel.get_by_role("link", name="Ouvrir AdGuard Home dans le navigateur")
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("href", "https://jabba.exemple.ts.net:3000")


def test_arrange_the_workbench_cards(page):
    """« Ranger » : une carte descend avec ↓, « Terminé » enregistre, l'ordre revient au rechargement."""
    go_to(page, "outils")
    grid = page.locator("#grille-etabli")
    expect(grid.locator(":scope > article").first).to_be_visible()
    first_ = grid.locator(":scope > article").first.evaluate(
        "e => e.dataset.carte || e.dataset.offre || e.dataset.integre"
    )
    page.get_by_role("button", name="Ranger").click()
    expect(grid).to_have_class(re.compile("rangement"))
    grid.locator(":scope > article").first.locator("[data-descendre]").click()
    second = (
        grid.locator(":scope > article").nth(1).evaluate("e => e.dataset.carte || e.dataset.offre || e.dataset.integre")
    )
    assert second == first_
    page.get_by_role("button", name="Terminé").click()
    expect(page.get_by_role("button", name="Ranger")).to_be_visible()
    page.reload()
    expect(grid.locator(":scope > article").first).to_be_visible()
    rank = (
        grid.locator(":scope > article").nth(1).evaluate("e => e.dataset.carte || e.dataset.offre || e.dataset.integre")
    )
    assert rank == first_  # gardé sur le serveur
    # Remise en place pour les autres tests : la carte remonte.
    page.get_by_role("button", name="Ranger").click()
    grid.locator(":scope > article").nth(1).locator("[data-monter]").click()
    page.get_by_role("button", name="Terminé").click()
    expect(page.get_by_role("button", name="Ranger")).to_be_visible()


def test_hide_a_card_and_arrange_home(page):
    """« Masquer » cache la carte hors rangement ; « Afficher » la remet. L'Accueil se range aussi."""
    go_to(page, "outils")
    grid = page.locator("#grille-etabli")
    card = grid.locator('[data-carte="dockge"]')
    expect(card).to_be_visible()
    page.get_by_role("button", name="Ranger").click()
    card.get_by_role("button", name="Masquer").click()
    page.get_by_role("button", name="Terminé").click()
    expect(card).to_be_hidden()
    page.reload()
    expect(grid.locator(":scope > article").first).to_be_visible()
    expect(grid.locator('[data-carte="dockge"]')).to_be_hidden()  # gardé sur le serveur
    page.get_by_role("button", name="Ranger").click()
    grid.locator('[data-carte="dockge"]').get_by_role("button", name="Afficher").click()
    page.get_by_role("button", name="Terminé").click()
    expect(grid.locator('[data-carte="dockge"]')).to_be_visible()

    go_to(page, "accueil")
    measures = page.locator("#mesures-accueil")
    expect(measures.locator(":scope > [data-carte]").first).to_have_attribute("data-carte", "processeur")
    page.get_by_role("button", name="Ranger").click()
    measures.locator('[data-carte="processeur"]').get_by_role("button", name="Descendre Processeur").click()
    page.get_by_role("button", name="Terminé").click()
    page.reload()
    expect(measures.locator(":scope > [data-carte]").nth(1)).to_have_attribute("data-carte", "processeur")
    page.get_by_role("button", name="Ranger").click()
    measures.locator('[data-carte="processeur"]').get_by_role("button", name="Monter Processeur").click()
    page.get_by_role("button", name="Terminé").click()
    expect(measures.locator(":scope > [data-carte]").first).to_have_attribute("data-carte", "processeur")


def test_tool_addresses(page):
    """Une adresse à part par outil : on colle une clé Tailscale, Jabba crée les adresses puis l'oublie.
    (En dernier : les liens des outils changent ensuite pour la suite de la démo.)"""
    go_to(page, "reglages?r=internet")
    card = page.locator("#carte-adresses")
    expect(card).to_contain_text("AdGuard Home")
    if page.viewport_size["width"] < 700:  # même serveur de démo pour les deux écrans : déjà créées au 1er passage
        expect(card).to_contain_text("adguard-home.exemple.ts.net")
        return
    expect(card).to_contain_text("À créer")
    link = card.get_by_role("link", name="Ouvrir la page des clés Tailscale")
    expect(link).to_have_attribute("href", "https://login.tailscale.com/admin/settings/keys")
    key = card.locator("#adresses-cle")
    assert key.get_attribute("type") == "text"  # pas un champ « mot de passe » : le trousseau ne s'en mêle pas
    key.fill("pas-une-cle")
    card.get_by_role("button", name="Créer les adresses").click()
    expect(page.get_by_text("commence par « tskey-api- »")).to_be_visible()
    key.fill("tskey-api-kDemo123456-AbCdEfGhIjKl")
    card.get_by_role("button", name="Créer les adresses").click()
    expect(page.locator("#carte-adresses")).to_contain_text("adguard-home.exemple.ts.net", timeout=15000)
    expect(page.locator("#carte-adresses #adresses-cle")).to_have_count(0)  # tout est créé : plus rien à coller
    go_to(page, "outils")
    outil = page.locator("article.outil", has=page.locator('[data-menu="adguard-home"]'))
    expect(outil.get_by_role("link", name="Ouvrir")).to_have_attribute("href", "https://adguard-home.exemple.ts.net")


def test_my_codes(page):
    """Système → Mes codes : trois groupes, ce que fait chaque code, jamais sa valeur."""
    go_to(page, "reglages?r=securite")
    card = page.locator("#mes-codes")
    expect(card).to_contain_text("À garder dans ton trousseau")
    expect(card).to_contain_text("Gardés par Jabba")
    expect(card).to_contain_text("Tu peux les supprimer de ton trousseau")
    expect(card).to_contain_text("Mot de passe du serveur")
    expect(card).to_contain_text("Compte AdGuard Home")
    expect(card).not_to_contain_text("demo-adguard-home")  # le mot de passe de la démo n'apparaît jamais


def test_paused_tools_after_running_ones(page):
    """Établi : un outil en pause passe après ceux qui tournent, chacun gardant l'ordre rangé."""
    go_to(page, "outils")
    order = page.evaluate(
        """async () => {
          const base = document.querySelector('script[type=module][src*="main.js"]').src.replace(/main\\.js.*$/, "");
          const { applyArrangement } = await import(base + "components/card_order.js");
          const grille = document.createElement("div");
          for (const [id, pause] of [["a", true], ["b", false], ["c", true], ["d", false]]) {
            const carte = document.createElement("article");
            carte.dataset.carte = id;
            if (pause) carte.classList.add("eteint");
            grille.appendChild(carte);
          }
          const rangement = { order: ["c", "a", "d", "b"], hidden_items: [] };
          applyArrangement(grille, rangement, { pausedLast: true });
          const apres = [...grille.children].map((c) => c.dataset.carte).join("");
          applyArrangement(grille, rangement);
          return [apres, [...grille.children].map((c) => c.dataset.carte).join("")];
        }"""
    )
    assert order == ["dbca", "cadb"]  # ailleurs (Accueil) : l'ordre rangé seul


def test_connect_a_device_to_nextcloud(page):
    """Nextcloud → « Connecter un ordinateur ou un téléphone » : les étapes de cet appareil, l'adresse à coller."""
    go_to(page, "outils")
    card = page.locator("article.outil", has=page.locator('[data-menu="nextcloud"]'))
    card.locator('[data-menu="nextcloud"]').click()
    card.get_by_role("menuitem", name="Connecter un ordinateur ou un téléphone").click()
    panel = card.locator(".panneau-outil")
    expect(panel).to_contain_text("Sur cet appareil")
    expect(panel).to_contain_text("Tout synchroniser")  # ordinateur de test : le dossier façon Dropbox
    adresse = panel.locator("[data-adresse]")
    expect(adresse).to_have_value(re.compile(r"^https://"))
    expect(panel).to_contain_text("qu'avec Tailscale allumé")  # démo : pas de domaine ouvert
    panel.get_by_text("iPhone / iPad").click()
    expect(panel.get_by_role("link", name="Nextcloud sur l'App Store")).to_have_attribute(
        "href", "https://apps.apple.com/app/id1125420102"
    )


def test_printable_codes_sheet(page):
    """Mes codes → « Fiche à imprimer » : les codes à garder, une ligne vide chacun, et rien d'autre à l'impression."""
    go_to(page, "reglages?r=securite")
    page.evaluate("window.print = () => { window.imprime = true; }")
    card = page.locator("#mes-codes")
    card.get_by_role("button", name="Fiche à imprimer").click()
    assert page.evaluate("window.imprime") is True
    sheet = page.locator("#fiche-codes")
    expect(sheet).to_be_hidden()  # à l'écran : rien ne change
    page.emulate_media(media="print")
    expect(sheet).to_be_visible()
    expect(sheet).to_contain_text("Mot de passe du serveur")
    expect(sheet).to_contain_text("Compte Tailscale")
    assert sheet.locator(".ligne-a-remplir").count() >= 3
    expect(page.locator("main")).to_be_hidden()  # seule la fiche s'imprime
    page.emulate_media(media="screen")
