"""Accès par le domaine : Caddy devant Nextcloud seulement, réglages de Nextcloud, liste de vérification."""

import shutil
import subprocess

import pytest

from host import actions, actions_tools
from services import domain_access


def test_caddy_ne_sert_que_l_adresse_de_nextcloud():
    commande, _ = actions.build("acces_domaine", {"adresse": "Cloud.Exemple.fr", "port": 8081})
    assert commande[:3] == ["sh", "-c", actions_tools.script("domain_install")] and commande[3] == actions_tools.DOMAIN_FOLDER
    caddyfile = commande[5]
    assert "cloud.exemple.fr {" in caddyfile and "reverse_proxy 127.0.0.1:8081" in caddyfile
    assert caddyfile.count(" {\n") == 1 + 1 + 1  # réglages généraux, l'adresse, son certificat : un seul site
    assert "https_port 8443" in caddyfile and "disable_http_challenge" in caddyfile
    assert "exemple" not in commande[2]  # le contenu passe en argument, jamais dans le texte du script
    assert "8090" not in caddyfile  # Jabba n'est jamais servi


@pytest.mark.parametrize(
    "adresse",
    ["exemple.fr; reboot", "localhost", "exemple", "-x.fr", "a..fr", "exemple.fr\n:80 {", "x" * 300 + ".fr", ""],
)
def test_adresse_refusee(adresse):
    with pytest.raises(actions.ActionError):
        actions.build("acces_domaine", {"adresse": adresse, "port": 8081})


@pytest.mark.parametrize("port", [22, 8443, "8081", True, 70000])
def test_port_refuse(port):
    with pytest.raises(actions.ActionError):
        actions.build("acces_domaine", {"adresse": "cloud.exemple.fr", "port": port})


@pytest.mark.skipif(not shutil.which("caddy"), reason="caddy absent (vérifié à la main)")
def test_le_fichier_de_caddy_est_valide(tmp_path):
    fichier = tmp_path / "Caddyfile"
    fichier.write_text(actions_tools.caddyfile("cloud.exemple.fr", 8081))
    sortie = subprocess.run(
        ["caddy", "validate", "--config", str(fichier), "--adapter", "caddyfile"], capture_output=True, text=True
    )
    assert sortie.returncode == 0, sortie.stderr[-500:]


def test_nextcloud_seulement_quelques_reglages_systeme():
    actions.build("nextcloud", {"commande": "config:system:set", "arguments": ["trusted_domains", "1", "--value=x.fr"]})
    for cle in ("dbpassword", "datadirectory", "maintenance", ""):
        with pytest.raises(actions.ActionError):
            actions.build("nextcloud", {"commande": "config:system:set", "arguments": [cle, "--value=x"]})
    with pytest.raises(actions.ActionError):
        actions.build("nextcloud", {"commande": "config:system:get", "arguments": []})


@pytest.fixture
def serveur(tmp_path, monkeypatch):
    monkeypatch.setattr(domain_access, "SETTINGS_FILE", str(tmp_path / "domaine.json"))
    monkeypatch.setattr(domain_access.config, "DEMO", False)
    nextcloud = {"trusted_domains": ["localhost", "serveur-test.ts.net"], "trusted_proxies": []}
    demandes = []

    def occ(args, **k):
        commande, cle = args[0], (args[1] if len(args) > 1 else "")
        if commande.startswith("app:"):
            nextcloud.setdefault("applis", []).append((commande, cle))
            return 0, "", ""
        if commande == "config:system:get":
            return (0, "\n".join(nextcloud[cle]) + "\n", "") if cle in nextcloud else (1, "", "")
        if commande == "config:system:delete":
            nextcloud[cle] = []
        if commande == "config:system:set":
            index, valeur = int(args[2]), args[3].removeprefix("--value=")
            liste = nextcloud[cle]
            liste[index : index + 1] = [valeur]
        return 0, "", ""

    def action(nom, **args):
        demandes.append((nom, args))
        return 0, "", ""

    monkeypatch.setattr(domain_access.commands, "occ", occ)
    monkeypatch.setattr(domain_access.commands, "run_action", action)
    return {"nextcloud": nextcloud, "demandes": demandes}


def test_ouvrir_puis_fermer(serveur):
    reglages = domain_access.enable("Exemple.fr", "cloud")
    assert reglages == {"domaine": "exemple.fr", "prefixe": "cloud", "actif": True}
    assert serveur["demandes"] == [("acces_domaine", {"adresse": "cloud.exemple.fr", "port": 8081})]
    assert serveur["nextcloud"]["trusted_domains"] == ["localhost", "serveur-test.ts.net", "cloud.exemple.fr"]
    assert serveur["nextcloud"]["trusted_proxies"] == domain_access.LOCAL_PROXIES
    assert serveur["nextcloud"]["applis"] == [("app:install", "twofactor_totp")]  # double vérification
    domain_access.enable("exemple.fr", "cloud")  # une 2e fois : rien en double
    assert serveur["nextcloud"]["trusted_domains"].count("cloud.exemple.fr") == 1
    assert len(serveur["nextcloud"]["trusted_proxies"]) == 2
    domain_access.disable()
    assert serveur["nextcloud"]["trusted_domains"] == [
        "localhost",
        "serveur-test.ts.net",
    ]  # les autres adresses restent
    assert serveur["demandes"][-1] == ("retirer_acces_domaine", {})
    assert domain_access.read()["actif"] is False


def test_double_verification_deja_la(serveur, monkeypatch):
    """Déjà installée (ou boutique injoignable) : app:install échoue, Jabba l'active simplement."""
    appels = []

    def occ(args, **k):
        if args[0].startswith("app:"):
            appels.append(tuple(args))
            return (1 if args[0] == "app:install" else 0), "", ""
        return 0, "", ""

    monkeypatch.setattr(domain_access.commands, "occ", occ)
    domain_access.enable("exemple.fr", "cloud")
    assert appels == [("app:install", "twofactor_totp"), ("app:enable", "twofactor_totp")]


def test_prefixe_refuse(serveur):
    for prefixe in ("a b", "x;y", "-z"):
        with pytest.raises(ValueError):
            domain_access.enable("exemple.fr", prefixe)
    assert serveur["demandes"] == []


def test_liste_de_verification(serveur, monkeypatch):
    domain_access.enable("exemple.fr", "cloud")
    monkeypatch.setattr(domain_access, "public_ip", lambda: "203.0.113.7")
    monkeypatch.setattr(domain_access.guest_network, "wifi_address", lambda: ("eth0", "192.168.1.20"))
    monkeypatch.setattr(domain_access, "certificate_ok", lambda name: False)
    monkeypatch.setattr(domain_access, "resolve", lambda name: ["198.51.100.1"])  # ancienne adresse
    etat = domain_access.status()
    assert (
        etat["adresse"] == "cloud.exemple.fr" and etat["ip_serveur"] == "192.168.1.20" and etat["port_serveur"] == 8443
    )
    assert not etat["dns_ok"] and not etat["certificat_ok"]
    monkeypatch.setattr(domain_access, "resolve", lambda name: ["203.0.113.7"])
    monkeypatch.setattr(domain_access, "certificate_ok", lambda name: True)
    etat = domain_access.status()
    assert etat["dns_ok"] and etat["certificat_ok"] and etat["protection_ok"] is True


def test_liens_de_partage_sur_l_adresse_publique(serveur):
    lien = "http://serveur-test.ts.net:8443/s/AbCd?dir=/x"
    assert domain_access.public_link(lien) == lien  # pas d'accès par le domaine : inchangé
    domain_access.enable("exemple.fr", "cloud")
    assert domain_access.public_link(lien) == "https://cloud.exemple.fr/s/AbCd?dir=/x"
    assert domain_access.public_link(None) is None
