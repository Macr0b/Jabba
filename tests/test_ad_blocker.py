"""AdGuard piloté par Jabba : installation à la place de l'assistant, appareils filtrés un par un."""

import json

import httpx
import pytest

from services import ad_blocker, optional_tools, tool_credentials


class FakeAdGuard:
    """Répond comme AdGuard Home ; garde la trace de ce que Jabba lui demande."""

    def __init__(self, first_start=False, password_ok=True):
        self.first_start = first_start
        self.password_ok = password_ok
        self.calls = []
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
        self.seen_ones = [{"ip": "192.168.1.20", "name": "iphone-de-rob"}, {"ip": "100.64.0.2", "name": ""}]

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.url.path, body))
        if request.url.path == "/control/install/get_addresses":
            return httpx.Response(200 if self.first_start else 404)
        if request.url.path == "/control/install/configure":
            self.first_start = False
            return httpx.Response(200)
        if not self.password_ok:
            return httpx.Response(401)
        if request.url.path == "/control/clients":
            return httpx.Response(200, json={"clients": self.clients, "auto_clients": self.seen_ones})
        return httpx.Response(200, json={})


@pytest.fixture
def adguard(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_credentials, "CREDENTIALS_FILE", str(tmp_path / "identifiants.json"))
    monkeypatch.setattr(ad_blocker.config, "DEMO", False)
    monkeypatch.setattr(ad_blocker, "_tailscale_names", lambda: {"100.64.0.2": "macbook"})
    monkeypatch.setattr(ad_blocker.time, "sleep", lambda _: None)
    fake = FakeAdGuard()
    monkeypatch.setattr(
        ad_blocker,
        "_client",
        lambda: httpx.Client(
            base_url=ad_blocker.ADDRESS, transport=httpx.MockTransport(fake), auth=("admin", ad_blocker._password())
        ),
    )
    tool_credentials.password_for("adguard-home")  # tiré au sort à l'activation
    return fake


def test_first_start_does_the_setup_and_turns_off_global_filtering(adguard):
    adguard.first_start = True
    assert ad_blocker.devices()["ready"]
    chemins = [chemin for chemin, _ in adguard.calls]
    assert chemins.index("/control/install/configure") < chemins.index("/control/filtering/config")
    setting = dict(adguard.calls)["/control/install/configure"]
    assert setting["username"] == "admin" and setting["password"] == tool_credentials.read("adguard-home")["password"]
    assert setting["dns"]["port"] == 53 and setting["web"]["port"] == 3000
    assert dict(adguard.calls)["/control/filtering/config"]["enabled"] is False  # aucun appareil filtré de base


def test_wizard_done_by_hand_global_filtering_turned_off_once(adguard):
    """Assistant d'AdGuard fait à la main : Jabba coupe quand même le filtrage général, une fois, puis n'y touche plus."""
    ad_blocker.devices()
    chemins = [chemin for chemin, _ in adguard.calls]
    assert "/control/install/configure" not in chemins
    assert dict(adguard.calls)["/control/filtering/config"]["enabled"] is False
    adguard.calls.clear()
    ad_blocker.devices()  # si l'admin le rallume ensuite dans AdGuard, c'est son choix
    assert "/control/filtering/config" not in [chemin for chemin, _ in adguard.calls]


def test_device_list(adguard):
    devices_ = {appareil["ip"]: appareil for appareil in ad_blocker.devices()["devices"]}
    assert devices_["100.64.0.2"] == {"ip": "100.64.0.2", "name": "macbook", "protected": True, "tailscale": True}
    assert devices_["192.168.1.20"] == {
        "ip": "192.168.1.20",
        "name": "iphone-de-rob",
        "protected": False,
        "tailscale": False,
    }
    assert "192.168.1.9" not in devices_  # un client réglé à la main dans AdGuard n'est pas celui de Jabba
    assert list(devices_)[0] == "100.64.0.2"  # les appareils filtrés d'abord


def test_turn_a_device_on_then_off(adguard):
    ad_blocker.set_protected("192.168.1.20", True)
    addition = [body for chemin, body in adguard.calls if chemin == "/control/clients/add"][-1]
    assert addition["name"] == "Jabba 192.168.1.20" and addition["ids"] == ["192.168.1.20"]
    assert addition["filtering_enabled"] is True and addition["use_global_settings"] is False
    adguard.calls.clear()
    ad_blocker.set_protected("192.168.1.20", False)
    chemins = [chemin for chemin, _ in adguard.calls]
    assert "/control/clients/delete" in chemins and "/control/clients/add" not in chemins


@pytest.mark.parametrize("adresse", ["192.168.1", "../x", "", None, "1.2.3.4; rm -rf /"])
def test_address_refused(adguard, adresse):
    with pytest.raises(ad_blocker.AdBlockerError):
        ad_blocker.set_protected(adresse, True)


def test_password_refused_or_unknown(adguard, tmp_path):
    adguard.password_ok = False
    result = ad_blocker.devices()
    assert not result["ready"] and "Mot de passe d'AdGuard refusé" in result["message"]


def test_first_start_without_a_kept_password(adguard, tmp_path):
    """Rallumé sans réinstallation (ou installé avant la 0.16.2) : Jabba tire le mot de passe au sort à ce moment-là."""
    (tmp_path / "identifiants.json").unlink()
    adguard.first_start = True
    assert ad_blocker.devices()["ready"]
    word = tool_credentials.read("adguard-home")["password"]
    assert len(word) == 24 and dict(adguard.calls)["/control/install/configure"]["password"] == word


def test_adguard_missing(adguard, monkeypatch):
    def refused(request):
        raise httpx.ConnectError("refusé")

    monkeypatch.setattr(
        ad_blocker, "_client", lambda: httpx.Client(base_url=ad_blocker.ADDRESS, transport=httpx.MockTransport(refused))
    )
    result = ad_blocker.devices()
    assert not result["ready"] and "ne répond pas" in result["message"]


def test_enable_without_typing_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_credentials, "CREDENTIALS_FILE", str(tmp_path / "identifiants.json"))
    monkeypatch.setattr(tool_credentials.config, "DEMO", False)
    started = []
    monkeypatch.setattr(
        optional_tools.background_tasks, "start_task", lambda *args, **k: started.append(args) or {"id": "t"}
    )
    optional_tools.install("adguard-home", "rob")
    assert ad_blocker._password() == started[0][-1] and len(started[0][-1]) == 24
