"""Double vérification dans le navigateur : Face ID simulé (authentificateur virtuel de Chromium) et appli d'authentification."""

import os
import sys
import time

import pytest
from playwright.sync_api import expect
from ui_tools import wait_until

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "app"))
from security import totp  # noqa: E402


@pytest.fixture
def localhost_page(browser, jabba_app):
    """Une page sur « localhost » (une clé d'accès refuse une adresse IP), avec un Face ID simulé."""
    pw, b = browser
    ctx = b.new_context(viewport={"width": 1280, "height": 800}, permissions=["clipboard-read", "clipboard-write"])
    p = ctx.new_page()
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.on("console", lambda m: errors.append(m.text) if "Content Security Policy" in m.text else None)
    cdp = ctx.new_cdp_session(p)
    cdp.send("WebAuthn.enable")
    cdp.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "internal",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
                "automaticPresenceSimulation": True,
            }
        },
    )
    p.root = jabba_app.replace("127.0.0.1", "localhost")
    yield p
    # on retire tout, pour que les autres tests se connectent avec le seul mot de passe
    p.evaluate(
        """async () => {
          const h = {"X-Jabba": "1"};
          const etat = await (await fetch("/api/double")).json();
          for (const cle of etat.cles || []) await fetch(`/api/double/cle/${cle.id}/retirer`, {method: "POST", headers: h});
          if (etat.appli) await fetch("/api/double/appli/retirer", {method: "POST", headers: h});
        }"""
    )
    ctx.close()
    assert not errors, "Erreurs JavaScript : " + " | ".join(errors)


def log_in(p):
    p.goto(f"{p.root}/?r={time.time()}#/reglages?r=moi")  # vraie recharge, même après une déconnexion
    p.wait_for_selector("#connexion", state="attached")
    p.evaluate("document.querySelector('#connexion').click()")
    window = p.locator("dialog[open]")
    # la fenêtre affiche bien son texte et ses boutons (régression corrigée en 0.13.0)
    expect(window.locator("p").first).to_contain_text("La connexion reste mémorisée 30 jours")
    expect(window.locator("button.lien")).to_have_text("Mot de passe oublié ?")
    expect(window.locator("button.btn-p")).to_have_text("Continuer")
    p.fill("input[name=username]", "serveur-demo")
    p.fill("input[name=password]", "demo-mot-de-passe")
    # on attend la réponse : la fenêtre se ferme avant, et recharger trop tôt couperait la connexion
    with p.expect_response(lambda r: "/api/admin/connexion" in r.url):
        p.press("input[name=password]", "Enter")


def test_face_id_and_app(localhost_page):
    p = localhost_page
    log_in(p)
    wait_until(p, "!document.querySelector('dialog[open]')")
    p.goto(p.root + "/#/reglages?r=moi")
    expect(p.locator("#double h2")).to_have_text("Double vérification")

    # Face ID
    p.click("#ajouter-cle")
    expect(p.locator("dialog[open] input[name=name]")).to_have_value(
        "Mac" if "Macintosh" in p.evaluate("navigator.userAgent") else "Clé d'accès"
    )
    p.fill("dialog[open] input[name=name]", "Mac de test")
    p.click("dialog[open] button.btn-p")
    expect(p.locator("dialog[open] .codes-secours li")).to_have_count(10)
    p.click("dialog[open] button.btn-p")
    expect(p.locator("#double")).to_contain_text("Mac de test")
    expect(p.locator("#double")).to_contain_text("10 restants")

    # appli d'authentification
    p.click("#ajouter-appli")
    expect(p.locator("dialog[open] img.qr-double")).to_be_visible()
    wait_until(p, "document.querySelector('dialog[open] img.qr-double').naturalWidth > 0")
    secret = p.locator("dialog[open] .cle-texte").inner_text().replace(" ", "")
    p.fill("#code-appli", "000000")
    p.click("dialog[open] button.btn-p")
    expect(p.locator(".toast").last).to_contain_text("Code incorrect")
    p.fill("#code-appli", totp.code_at(secret, time.time()))
    p.click("dialog[open] button.btn-p")
    wait_until(p, "!document.querySelector('dialog[open]')")
    expect(p.locator("#double")).to_contain_text("Appli d'authentification")
    expect(p.locator("#ajouter-appli")).to_have_count(0)

    # déconnexion, puis connexion avec Face ID
    p.evaluate("fetch('/api/admin/deconnexion', {method: 'POST', headers: {'X-Jabba': '1'}})")
    p.wait_for_timeout(300)
    log_in(p)
    expect(p.locator("dialog[open] h2")).to_have_text("Deuxième vérification")
    expect(p.locator("#autre-code")).to_have_text("Utiliser un code de secours")
    p.click("#avec-cle")
    wait_until(p, "!document.querySelector('dialog[open]')")
    p.wait_for_timeout(500)
    p.goto(p.root + "/#/reglages?r=moi")
    expect(p.locator("#qui")).to_contain_text("serveur-demo")

    # puis avec l'appli : le code du créneau suivant (celui d'avant a servi)
    p.evaluate("fetch('/api/admin/deconnexion', {method: 'POST', headers: {'X-Jabba': '1'}})")
    p.wait_for_timeout(300)
    log_in(p)
    expect(p.locator("dialog[open] h2")).to_have_text("Deuxième vérification")
    p.fill("#code-double", totp.code_at(secret, time.time() + 30))
    p.click("dialog[open] button[value=oui]")
    wait_until(p, "!document.querySelector('dialog[open]')")
    p.wait_for_timeout(500)
    p.goto(p.root + "/#/reglages?r=moi")
    expect(p.locator("#double")).to_contain_text("Active")
