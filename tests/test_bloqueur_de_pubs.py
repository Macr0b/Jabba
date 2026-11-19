"""AdGuard piloté par Jabba : installation à la place de l'assistant, appareils filtrés un par un."""

import json

import httpx
import pytest

from services import ad_blocker, optional_tools, tool_credentials


class FauxAdGuard:
    """Répond comme AdGuard Home ; garde la trace de ce que Jabba lui demande."""

    def __init__(self, premier_lancement=False, mot_de_passe_ok=True):
        self.premier_lancement = premier_lancement
        self.mot_de_passe_ok = mot_de_passe_ok
        self.appels = []
        self.clients = [
            {
                "name": "Jabba 100.64.0.2",
                "ids": ["100.64.0.2"],
                "use_global_settings": False,
                "filtering_enabled": True,
            },
            {
                "name": "Ancien réglage à la main",
                "ids": ["192.168.1.9"],
                "use_global_settings": False,
                "filtering_enabled": True,
            },
        ]
        self.vus = [{"ip": "192.168.1.20", "name": "iphone-de-rob"}, {"ip": "100.64.0.2", "name": ""}]

    def __call__(self, request):
        corps = json.loads(request.content) if request.content else None
        self.appels.append((request.url.path, corps))
        if request.url.path == "/control/install/get_addresses":
            return httpx.Response(200 if self.premier_lancement else 404)
        if request.url.path == "/control/install/configure":
            self.premier_lancement = False
            return httpx.Response(200)
        if not self.mot_de_passe_ok:
            return httpx.Response(401)
        if request.url.path == "/control/clients":
            return httpx.Response(200, json={"clients": self.clients, "auto_clients": self.vus})
        return httpx.Response(200, json={})


@pytest.fixture
def adguard(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_credentials, "CREDENTIALS_FILE", str(tmp_path / "identifiants.json"))
    monkeypatch.setattr(ad_blocker.config, "DEMO", False)
    monkeypatch.setattr(ad_blocker, "_tailscale_names", lambda: {"100.64.0.2": "macbook"})
    monkeypatch.setattr(ad_blocker.time, "sleep", lambda _: None)
    faux = FauxAdGuard()
    monkeypatch.setattr(
        ad_blocker,
        "_client",
        lambda: httpx.Client(
            base_url=ad_blocker.ADDRESS, transport=httpx.MockTransport(faux), auth=("admin", ad_blocker._password())
        ),
    )
    tool_credentials.password_for("adguard-home")  # tiré au sort à l'activation
    return faux


def test_premier_lancement_fait_l_installation_et_coupe_le_filtrage_general(adguard):
    adguard.premier_lancement = True
    assert ad_blocker.devices()["pret"]
    chemins = [chemin for chemin, _ in adguard.appels]
    assert chemins.index("/control/install/configure") < chemins.index("/control/filtering/config")
    reglage = dict(adguard.appels)["/control/install/configure"]
    assert (
        reglage["username"] == "admin" and reglage["password"] == tool_credentials.read("adguard-home")["mot_de_passe"]
    )
    assert reglage["dns"]["port"] == 53 and reglage["web"]["port"] == 3000
    assert dict(adguard.appels)["/control/filtering/config"]["enabled"] is False  # aucun appareil filtré de base


def test_deja_installe_on_ne_touche_pas_au_reglage_general(adguard):
    ad_blocker.devices()
    assert "/control/install/configure" not in [chemin for chemin, _ in adguard.appels]
    assert "/control/filtering/config" not in [chemin for chemin, _ in adguard.appels]


def test_liste_des_appareils(adguard):
    appareils = {appareil["ip"]: appareil for appareil in ad_blocker.devices()["appareils"]}
    assert appareils["100.64.0.2"] == {"ip": "100.64.0.2", "nom": "macbook", "protege": True, "tailscale": True}
    assert appareils["192.168.1.20"] == {
        "ip": "192.168.1.20",
        "nom": "iphone-de-rob",
        "protege": False,
        "tailscale": False,
    }
    assert "192.168.1.9" not in appareils  # un client réglé à la main dans AdGuard n'est pas celui de Jabba
    assert list(appareils)[0] == "100.64.0.2"  # les appareils filtrés d'abord


def test_allumer_puis_eteindre_un_appareil(adguard):
    ad_blocker.set_protected("192.168.1.20", True)
    ajout = [corps for chemin, corps in adguard.appels if chemin == "/control/clients/add"][-1]
    assert ajout["name"] == "Jabba 192.168.1.20" and ajout["ids"] == ["192.168.1.20"]
    assert ajout["filtering_enabled"] is True and ajout["use_global_settings"] is False
    adguard.appels.clear()
    ad_blocker.set_protected("192.168.1.20", False)
    chemins = [chemin for chemin, _ in adguard.appels]
    assert "/control/clients/delete" in chemins and "/control/clients/add" not in chemins


@pytest.mark.parametrize("adresse", ["192.168.1", "../x", "", None, "1.2.3.4; rm -rf /"])
def test_adresse_refusee(adguard, adresse):
    with pytest.raises(ad_blocker.AdBlockerError):
        ad_blocker.set_protected(adresse, True)


def test_mot_de_passe_refuse_ou_inconnu(adguard, tmp_path):
    adguard.mot_de_passe_ok = False
    resultat = ad_blocker.devices()
    assert not resultat["pret"] and "Mot de passe d'AdGuard refusé" in resultat["message"]
    adguard.premier_lancement = True
    (tmp_path / "identifiants.json").unlink()
    assert "Mot de passe d'AdGuard inconnu" in ad_blocker.devices()["message"]


def test_adguard_absent(adguard, monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refusé")

    monkeypatch.setattr(
        ad_blocker, "_client", lambda: httpx.Client(base_url=ad_blocker.ADDRESS, transport=httpx.MockTransport(refuse))
    )
    resultat = ad_blocker.devices()
    assert not resultat["pret"] and "ne répond pas" in resultat["message"]


def test_activer_sans_rien_taper(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_credentials, "CREDENTIALS_FILE", str(tmp_path / "identifiants.json"))
    monkeypatch.setattr(tool_credentials.config, "DEMO", False)
    lance = []
    monkeypatch.setattr(optional_tools.background_tasks, "start_task", lambda *args: lance.append(args) or {"id": "t"})
    optional_tools.install("adguard-home", "rob")
    assert ad_blocker._password() == lance[0][-1] and len(lance[0][-1]) == 24
