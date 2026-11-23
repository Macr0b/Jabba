"""Point 1 : un service extérieur prévient quand le serveur se tait. Point 3 : Nextcloud ouvert sur Internet
voit la vraie adresse des visiteurs et freine les essais en rafale."""

import json

import httpx
import pytest

from services import domain_access, heartbeat

ADRESSE = "https://hc-ping.com/0f9a3c1e-5b7d-4e2a-9c11-2d3e4f5a6b7c"


@pytest.fixture
def signal(tmp_path, monkeypatch):
    monkeypatch.setattr(heartbeat, "SETTINGS_FILE", str(tmp_path / "surveillance.json"))
    monkeypatch.setattr(heartbeat.config, "DEMO", False)

    class Recus(list):
        repond = True

    recus = Recus()

    def get(url, timeout=None, follow_redirects=None):
        recus.append(url)
        return httpx.Response(200 if recus.repond else 404)

    monkeypatch.setattr(heartbeat.httpx, "get", get)
    return recus


def test_adresse_essayee_puis_gardee_sans_jamais_revenir_a_l_ecran(signal, tmp_path):
    etat = heartbeat.save(f"  {ADRESSE} ")
    assert etat["pret"] and etat["ok"] and ADRESSE not in json.dumps(etat)
    assert signal == [ADRESSE]  # un premier signal, avant de garder
    assert oct((tmp_path / "surveillance.json").stat().st_mode)[-3:] == "600"


@pytest.mark.parametrize("adresse", ["", "http://hc-ping.com/abcd1234", "https://hc-ping.com/", "pas une adresse"])
def test_adresse_mal_recopiee(signal, adresse):
    with pytest.raises(heartbeat.ServiceError, match="en entier"):
        heartbeat.save(adresse)
    assert signal == [] and not heartbeat.status()["pret"]


def test_adresse_que_le_service_ne_connait_pas(signal):
    signal.repond = False
    with pytest.raises(heartbeat.ServiceError, match="n'a pas reçu"):
        heartbeat.save(ADRESSE)
    assert not heartbeat.status()["pret"]


def test_un_signal_toutes_les_5_minutes(signal):
    assert heartbeat.watch(now=1_000) is None  # rien en place : aucun appel
    heartbeat.save(ADRESSE)
    signal.clear()
    assert heartbeat.watch(now=10_000) is True
    assert heartbeat.watch(now=10_060) is None  # une minute après : rien
    assert heartbeat.watch(now=10_300) is True and len(signal) == 2
    signal.repond = False
    assert heartbeat.watch(now=10_600) is False
    etat = heartbeat.status()
    assert etat["ok"] is False and etat["dernier"] == 10_300  # le dernier signal reçu reste affiché
    heartbeat.forget()
    assert not heartbeat.status()["pret"] and heartbeat.watch(now=20_000) is None


@pytest.fixture
def nextcloud(monkeypatch):
    reglages = {
        "trusted_proxies": ["172.16.0.0/12"],
        "auth.bruteforce.protection.enabled": [],
        "forwarded_for_headers": [],
    }
    etat = {"en_marche": True}

    def occ(args, **k):
        if args == ["status"]:
            return (0 if etat["en_marche"] else 1), "", ""
        if args[0] == "config:system:get":
            valeurs = reglages.get(args[1], [])
            return (0 if valeurs else 1), "\n".join(valeurs), ""
        if args[0] == "config:system:set":
            reglages.setdefault(args[1], []).append(args[3].removeprefix("--value="))
        return 0, "", ""

    monkeypatch.setattr(domain_access.config, "DEMO", False)
    monkeypatch.setattr(domain_access.commands, "occ", occ)
    monkeypatch.setattr(domain_access.nextcloud_settings.commands, "occ", occ)
    return reglages, etat


def test_relais_de_confiance_remis_s_ils_manquent(nextcloud):
    reglages, _ = nextcloud
    assert domain_access.protection() is True
    assert reglages["trusted_proxies"] == ["172.16.0.0/12", "127.0.0.1"]  # Caddy ajouté, rien d'enlevé
    assert domain_access.protection() is True and len(reglages["trusted_proxies"]) == 2  # pas de doublon


def test_protection_coupee_signalee_jamais_forcee(nextcloud):
    reglages, etat = nextcloud
    reglages["auth.bruteforce.protection.enabled"] = ["false"]
    assert domain_access.protection() is False
    assert reglages["auth.bruteforce.protection.enabled"] == ["false"]  # le choix de l'admin reste le sien
    reglages["auth.bruteforce.protection.enabled"] = []
    reglages["forwarded_for_headers"] = ["HTTP_X_REAL_IP"]  # en-tête que Caddy n'envoie pas
    assert domain_access.protection() is False
    etat["en_marche"] = False
    assert domain_access.protection() is None


def test_alerte_si_la_protection_reste_coupee(nextcloud, monkeypatch):
    reglages, _ = nextcloud
    alertes = []
    monkeypatch.setattr(domain_access.notifications, "send", lambda *a, **k: alertes.append(a[1]))
    monkeypatch.setattr(domain_access, "read", lambda: {"actif": False})
    reglages["auth.bruteforce.protection.enabled"] = ["false"]
    domain_access.watch_protection()
    assert alertes == []  # accès fermé : Nextcloud n'est pas sur Internet
    monkeypatch.setattr(domain_access, "read", lambda: {"actif": True, "domaine": "exemple.fr"})
    domain_access.watch_protection()
    assert alertes == ["Nextcloud : protection contre les essais en rafale coupée"]
