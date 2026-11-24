"""Écran Fichiers : envois avec avancement, tri mémorisé, grille, sélection multiple, Annuler, clavier, clic droit, .zip."""

import io
import urllib.parse
import uuid
import zipfile

import pytest
from playwright.sync_api import expect

FILES = [
    {"name": "b-moyen.txt", "mimeType": "text/plain", "buffer": b"B" * 2000},
    {"name": "a-petit.txt", "mimeType": "text/plain", "buffer": b"A" * 10},
    {"name": "c-gros.txt", "mimeType": "text/plain", "buffer": b"C" * 50000},
]


@pytest.fixture
def dossier(page):
    """Un dossier neuf rempli par l'écran lui-même (bouton « Envoyer des fichiers »)."""
    chemin = f"/Films et séries/Essais {uuid.uuid4().hex[:6]}"
    page.evaluate(
        """async (chemin) => { await fetch("/api/fichiers/dossier", {method: "POST",
           headers: {"X-Jabba": "1", "Content-Type": "application/json"}, body: JSON.stringify({chemin})}); }""",
        chemin,
    )
    page.goto(page.root + "/#/fichiers?chemin=" + urllib.parse.quote(chemin))
    expect(page.get_by_text("Ce dossier est vide.")).to_be_visible()
    page.set_input_files("#choix", FILES)
    expect(page.locator(".envois")).to_contain_text("3 / 3 envoyés")
    expect(page.locator(".element")).to_have_count(3)
    expect(page.locator(".envois")).to_have_count(0, timeout=6000)  # le panneau se retire tout seul
    yield chemin
    page.evaluate(
        """async (chemin) => { await fetch("/api/fichiers/supprimer", {method: "POST",
           headers: {"X-Jabba": "1", "Content-Type": "application/json"}, body: JSON.stringify({chemin, dossier: true})}); }""",
        chemin,
    )


def test_sort_remembered_and_grid(page, dossier):
    expect(page.locator(".element a")).to_have_text(["a-petit.txt", "b-moyen.txt", "c-gros.txt"])
    page.select_option("#tri", "taille:-1")
    expect(page.locator(".element a").first).to_have_text("c-gros.txt")
    page.reload()
    expect(page.locator(".element a").first).to_have_text("c-gros.txt")  # gardé pour ce dossier
    page.click("[data-vue=grille]")
    expect(page.locator(".grille-fichiers .tuile-fichier")).to_have_count(3)
    page.reload()
    expect(page.locator(".grille-fichiers .tuile-fichier")).to_have_count(3)
    page.click("[data-vue=liste]")
    expect(page.locator(".grille-fichiers")).to_have_count(0)
    if page.appareil == "ordinateur":
        page.click("button[data-tri=nom]")
        expect(page.locator(".element a")).to_have_text(["a-petit.txt", "b-moyen.txt", "c-gros.txt"])
        page.click("button[data-tri=nom]")
        expect(page.locator(".element a")).to_have_text(["c-gros.txt", "b-moyen.txt", "a-petit.txt"])


def select(page, index):
    """Ordinateur : la case à cocher. iPhone : appui long pour commencer, puis un simple toucher."""
    element = page.locator(".element").nth(index)
    if page.appareil == "ordinateur":
        element.hover()
        element.locator(".case").click()
    elif page.locator("body.selection-active").count():
        element.locator(".nom").tap()
    else:
        element.dispatch_event("touchstart")
        page.wait_for_timeout(700)
        element.dispatch_event("touchend")
        element.dispatch_event("click")


def test_selection_trash_and_undo(page, dossier):
    select(page, 0)
    expect(page.locator("#barre-selection")).to_contain_text("1 sélectionné")
    select(page, 2)
    expect(page.locator("#barre-selection")).to_contain_text("2 sélectionnés")
    page.click("#barre-selection [data-action=corbeille]")
    expect(page.locator(".toast").last).to_contain_text("2 éléments mis à la corbeille")
    expect(page.locator(".element")).to_have_count(1)
    page.locator(".toast-action").last.click()
    expect(page.locator(".element")).to_have_count(3)
    expect(page.locator(".toast").last).to_contain_text("2 éléments remis en place")


def test_keyboard_right_click_and_zip(page, dossier):
    if page.appareil != "ordinateur":
        pytest.skip("clavier et clic droit : sur ordinateur")
    page.locator(".element").first.focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press(" ")
    expect(page.locator("#barre-selection")).to_contain_text("1 sélectionné")
    expect(page.locator(".element .case").nth(1)).to_be_checked()
    page.keyboard.press("Control+a")
    expect(page.locator("#barre-selection")).to_contain_text("3 sélectionnés")
    page.keyboard.press("Escape")
    expect(page.locator("#barre-selection")).to_have_count(0)

    # F2 renomme
    page.locator(".element").first.focus()
    page.keyboard.press("F2")
    expect(page.locator("dialog[open] h2")).to_have_text("Renommer")
    page.keyboard.press("Escape")

    # clic droit : le menu de l'élément ; avec une sélection, celui de la sélection
    page.locator(".element").first.click(button="right")
    expect(page.locator("dialog[open]")).to_contain_text("Mettre à la corbeille")
    page.keyboard.press("Escape")
    page.locator(".element").first.focus()
    page.keyboard.press("Control+a")
    page.locator(".element").first.click(button="right")
    expect(page.locator("dialog[open]")).to_contain_text("Télécharger (3, en .zip)")
    page.keyboard.press("Escape")

    with page.expect_download() as pending:
        page.click("#barre-selection [data-action=telecharger]")
    archive = zipfile.ZipFile(io.BytesIO(open(pending.value.path(), "rb").read()))
    assert sorted(archive.namelist()) == ["a-petit.txt", "b-moyen.txt", "c-gros.txt"]
    assert archive.read("c-gros.txt") == b"C" * 50000

    # Suppr sur l'élément sélectionné au clavier, puis Annuler
    page.keyboard.press("Escape")
    page.locator(".element").first.focus()
    page.keyboard.press("Delete")
    expect(page.locator(".element")).to_have_count(2)
    page.locator(".toast-action").last.click()
    expect(page.locator(".element")).to_have_count(3)


def test_upload_error_then_retry(page, dossier):
    """Un fichier déjà présent est refusé : il reste dans le panneau avec « Réessayer », les autres passent."""
    page.set_input_files("#choix", [FILES[0], {"name": "nouveau.txt", "mimeType": "text/plain", "buffer": b"N"}])
    expect(page.locator(".envois")).to_contain_text("1 / 2 envoyé")
    expect(page.locator(".envoi[data-etat=erreur] [data-reessayer]")).to_be_visible()
    expect(page.locator(".element")).to_have_count(4)
    page.click("#fermer-envois")
    expect(page.locator(".envois")).to_have_count(0)


def create_folder(page, chemin):
    page.evaluate(
        """async (chemin) => { await fetch("/api/fichiers/dossier", {method: "POST",
           headers: {"X-Jabba": "1", "Content-Type": "application/json"}, body: JSON.stringify({chemin})}); }""",
        chemin,
    )


def test_move_with_the_folder_picker(page, dossier):
    create_folder(page, dossier + "/Archives")
    create_folder(page, dossier + "/Archives/2026")
    page.reload()
    expect(page.locator(".element")).to_have_count(4)
    line = page.locator(".element", has_text="a-petit.txt")
    line.locator(".btn-menu").click()
    page.locator("dialog[open] button", has_text="Déplacer vers…").click()
    window = page.locator("dialog[open]")
    expect(window.locator("h2")).to_have_text("Déplacer « a-petit.txt »")
    window.locator(".ligne-dossier", has_text="Archives").click()
    expect(window.locator(".chemin-choisi")).to_contain_text("/Archives")
    window.locator(".ligne-dossier", has_text="2026").click()
    window.locator("button[value=oui]").click()
    expect(page.locator(".toast").last).to_contain_text("1 élément déplacé")
    expect(page.locator(".element")).to_have_count(3)
    page.locator(".toast-action").last.click()  # Annuler
    expect(page.locator(".element")).to_have_count(4)


def test_a_folder_cannot_go_into_itself(page, dossier):
    create_folder(page, dossier + "/Archives")
    page.reload()
    page.locator(".element", has_text="Archives").locator(".btn-menu").click()
    page.locator("dialog[open] button", has_text="Déplacer vers…").click()
    expect(page.locator("dialog[open] .ligne-dossier", has_text="Archives")).to_have_count(0)  # pas proposé
    page.keyboard.press("Escape")


def test_drag_onto_a_folder_then_onto_the_breadcrumb(page, dossier):
    if page.appareil != "ordinateur":
        pytest.skip("glisser à la souris : sur ordinateur")
    create_folder(page, dossier + "/Archives")
    page.reload()
    expect(page.locator(".element")).to_have_count(4)
    page.locator(".element", has_text="b-moyen.txt").drag_to(page.locator(".element", has_text="Archives"))
    expect(page.locator(".toast").last).to_contain_text("1 élément déplacé")
    expect(page.locator(".element")).to_have_count(3)
    page.locator(".element", has_text="Archives").locator("a").click()
    expect(page.locator(".element")).to_have_count(1)
    # retour vers le dossier parent par le fil d'Ariane
    parent_name = dossier.rsplit("/", 1)[1]
    page.locator(".element", has_text="b-moyen.txt").drag_to(page.locator(".fil a", has_text=parent_name))
    expect(page.locator(".toast").last).to_contain_text("1 élément déplacé")
    expect(page.get_by_text("Ce dossier est vide.")).to_be_visible()


def test_upload_a_whole_folder(page, dossier, tmp_path):
    if page.appareil != "ordinateur":
        pytest.skip("choix d'un dossier : sur ordinateur")
    root = tmp_path / "Vacances"
    (root / "Jour 1").mkdir(parents=True)
    (root / "Jour 1" / "plage.txt").write_text("sable")
    (root / "carte.txt").write_text("carte")
    page.set_input_files("#choix-dossier", str(root))
    expect(page.locator(".element", has_text="Vacances")).to_be_visible()
    expect(page.locator(".envois")).to_contain_text("2 / 2 envoyés")
    page.locator(".element", has_text="Vacances").locator("a").click()
    expect(page.locator(".element a")).to_have_text(["Jour 1", "carte.txt"])
    page.locator(".element", has_text="Jour 1").locator("a").click()
    expect(page.locator(".element a")).to_have_text(["plage.txt"])


def test_select_all(page, dossier):
    """Un bouton visible, en plus de ⌘A : tout le dossier est sélectionné, la barre d'actions apparaît."""
    page.get_by_role("button", name="Tout sélectionner").first.click()
    bar = page.locator("#barre-selection")
    expect(bar).to_be_visible()
    total = page.locator(".element").count()
    expect(bar).to_contain_text(f"{total} sélectionné")
    expect(bar.get_by_role("button", name="Tout sélectionner")).to_have_count(0)  # déjà tout
    # Une fois tout choisi, le même bouton devient « Tout désélectionner » et vide la sélection.
    button_ = page.locator("#tout-selectionner")
    expect(button_).to_have_text("Tout désélectionner")
    expect(bar.get_by_role("button", name="Tout désélectionner")).to_be_visible()
    button_.click()
    expect(bar).to_have_count(0)
    expect(button_).to_have_text("Tout sélectionner")
    page.locator(".element input[data-choix]").first.evaluate(
        "e => e.click()"
    )  # une partie seulement : le bouton propose de tout prendre
    expect(button_).to_have_text("Tout sélectionner")
    page.locator("#barre-selection").get_by_role("button", name="Tout sélectionner").click()
    page.locator("#barre-selection").get_by_role("button", name="Tout désélectionner").click()
    expect(page.locator("#barre-selection")).to_have_count(0)
