"""Relais des alertes vers ntfy : sujet tiré au sort, envoi seulement des alertes pour tous, ntfy absent sans gêne."""

import socket
import threading

import httpx
import pytest

from routes import alerts as alerts_route
from services import notifications, ntfy_relay


@pytest.fixture
def relais(tmp_path, monkeypatch):
    monkeypatch.setattr(ntfy_relay, "TOPIC_FILE", str(tmp_path / "ntfy.json"))
    envois = []
    monkeypatch.setattr(ntfy_relay, "forward", lambda *args: envois.append(args))
    return envois


def test_sujet_tire_au_sort_et_garde(relais):
    sujet = ntfy_relay.topic()
    assert sujet.startswith("jabba-") and len(sujet) == 22
    assert ntfy_relay.topic() == sujet  # le même à chaque fois : l'abonnement du téléphone reste bon


def test_seules_les_alertes_pour_tous_passent_par_ntfy(relais, monkeypatch):
    monkeypatch.setattr(
        notifications, "_read", lambda: {"types": {"courant": True, "comptes": False}, "abonnements": []}
    )
    monkeypatch.setattr(notifications.activity_log, "record", lambda *a: None)
    notifications.send("courant", "Coupure de courant", "Sur batterie")
    notifications.send("courant", "Pour Rob seulement", "", recipients={"rob@exemple.fr"})
    notifications.send("comptes", "Type coupé", "")
    notifications.send("comptes", "Essai forcé", "", force=True)
    assert [envoi[1] for envoi in relais] == ["Coupure de courant", "Essai forcé"]


def test_envoi_titre_priorite_et_lien(tmp_path, monkeypatch):
    monkeypatch.setattr(ntfy_relay, "TOPIC_FILE", str(tmp_path / "ntfy.json"))
    recu = {}
    monkeypatch.setattr(
        ntfy_relay.httpx,
        "post",
        lambda url, content, headers, timeout: recu.update(url=url, texte=content, entetes=headers),
    )
    ntfy_relay._post("securite", "Accès refusé", "Un inconnu a essayé", "/#/systeme")
    assert recu["url"] == f"http://127.0.0.1:2586/{ntfy_relay.topic()}"
    assert recu["texte"] == "Un inconnu a essayé".encode() and recu["entetes"]["Title"] == "Accès refusé".encode()
    assert recu["entetes"]["Priority"] == "high" and "Click" not in recu["entetes"]  # lien relatif : pas de clic
    ntfy_relay._post("atelier", "Entretien", "", "https://serveur-test:9000/#/atelier")
    assert recu["texte"] == "Entretien".encode() and recu["entetes"]["Priority"] == "default"
    assert recu["entetes"]["Click"] == "https://serveur-test:9000/#/atelier"


def test_ntfy_absent_ne_gene_pas(tmp_path, monkeypatch):
    monkeypatch.setattr(ntfy_relay, "TOPIC_FILE", str(tmp_path / "ntfy.json"))

    def refuse(*args, **kwargs):
        raise httpx.ConnectError("refusé")

    monkeypatch.setattr(ntfy_relay.httpx, "post", refuse)
    ntfy_relay._post("courant", "Coupure", "", "/")  # aucune erreur


def test_sujet_montre_seulement_si_ntfy_tourne(relais, monkeypatch):
    ecoute = socket.socket()
    ecoute.bind(("127.0.0.1", 0))
    ecoute.listen()
    threading.Thread(target=lambda: ecoute.accept()[0].close(), daemon=True).start()
    monkeypatch.setattr(ntfy_relay, "ADDRESS", ecoute.getsockname())
    assert ntfy_relay.is_running()
    assert alerts_route.alert_settings()["ntfy"] == ntfy_relay.topic()
    ecoute.close()
    monkeypatch.setattr(ntfy_relay, "ADDRESS", ("127.0.0.1", 1))
    assert not ntfy_relay.is_running() and alerts_route.alert_settings()["ntfy"] == ""
