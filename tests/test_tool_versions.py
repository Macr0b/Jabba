"""Mises à jour des outils : lecture des références d'images, registre (jeton anonyme), alerte une seule fois."""

import json

import pytest
from fake_host_service import fake_service

from services import tool_updates


@pytest.mark.parametrize(
    "image, expected",
    [
        ("nextcloud", ("registry-1.docker.io", "library/nextcloud", "latest")),
        ("nextcloud:32-apache", ("registry-1.docker.io", "library/nextcloud", "32-apache")),
        ("jellyfin/jellyfin:10.10.7", ("registry-1.docker.io", "jellyfin/jellyfin", "10.10.7")),
        ("docker.io/louislam/dockge:1", ("registry-1.docker.io", "louislam/dockge", "1")),
        ("ghcr.io/jellyfin/jellyfin", ("ghcr.io", "jellyfin/jellyfin", "latest")),
        ("lscr.io/linuxserver/jellyfin:latest", ("lscr.io", "linuxserver/jellyfin", "latest")),
        ("localhost:5000/moi/outil:2", ("localhost:5000", "moi/outil", "2")),
        ("nextcloud:32@sha256:abc", ("registry-1.docker.io", "library/nextcloud", "32")),
    ],
)
def test_reference(image, expected):
    assert tool_updates.parse_reference(image) == expected


class Answer:
    def __init__(self, code, headers_=None, body=None):
        self.status_code = code
        self.headers = {k.lower(): v for k, v in (headers_ or {}).items()}
        self._body = body or {}

    def json(self):
        return self._body


class Registry:
    """Un registre qui exige un jeton anonyme, comme Docker Hub."""

    def __init__(self, fingerprints):
        self.fingerprints = fingerprints
        self.calls = []

    def __call__(self, url, params=None, headers=None, method="GET"):
        self.calls.append((method, url, params, dict(headers or {})))
        if url.startswith("https://auth.exemple/token"):
            assert params == {"service": "registry.docker.io", "scope": "repository:library/nextcloud:pull"}
            return Answer(200, body={"token": "jeton"})
        if (headers or {}).get("Authorization") != "Bearer jeton":
            return Answer(
                401,
                {
                    "WWW-Authenticate": 'Bearer realm="https://auth.exemple/token",service="registry.docker.io",scope="repository:library/nextcloud:pull"'
                },
            )
        tag = url.rsplit("/", 1)[-1]
        if tag not in self.fingerprints:
            return Answer(404)
        return Answer(200, {"Docker-Content-Digest": self.fingerprints[tag]})


def test_remote_digest_with_token():
    registry = Registry({"32": "sha256:neuf"})
    assert tool_updates.remote_digest("registry-1.docker.io", "library/nextcloud", "32", registry) == "sha256:neuf"
    assert registry.calls[-1][0] == "HEAD" and "manifest.list" in registry.calls[-1][3]["Accept"]
    assert tool_updates.remote_digest("registry-1.docker.io", "library/nextcloud", "absent", registry) is None


def test_non_https_realm_refused():
    def get(url, params=None, headers=None, method="GET"):
        return Answer(401, {"WWW-Authenticate": 'Bearer realm="http://pirate/token"'})

    assert tool_updates.remote_digest("r", "a/b", "1", get) is None


@pytest.fixture
def serveur(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_updates, "STATE_FILE", str(tmp_path / "maj.json"))
    monkeypatch.setattr(
        tool_updates.tools,
        "list_tools",
        lambda: [
            {"id": "nextcloud", "nom": "Nextcloud", "modifiable": True, "conteneurs": [{"nom": "nextcloud"}]},
            {"id": "jabba-app", "nom": "Appli Jabba", "modifiable": False, "conteneurs": [{"nom": "jabba"}]},
        ],
    )
    locales = {"empreintes": ["sha256:ancien"]}

    def host_name(commande, timeout=30, input_text=None):
        if commande[:2] == ["docker", "inspect"]:
            return 0, "nextcloud:32|sha256:" + "a" * 64 + "\n", ""
        if commande[:3] == ["docker", "image", "inspect"]:
            drops = json.dumps([f"nextcloud@{e}" for e in locales["empreintes"]])
            return 0, f"{drops}|32.0.1\n", ""
        return 1, "", ""

    fake_service(monkeypatch, host_name)
    alerts = []
    monkeypatch.setattr(tool_updates.notifications, "send", lambda *a, **k: alerts.append(a))
    return locales, alerts


def test_up_to_date_then_new_version_single_alert(serveur):
    locales, alerts = serveur
    etat = tool_updates.check_all(now=1, get=Registry({"32": "sha256:ancien"}))
    assert etat["outils"] == {
        "nextcloud": {"nom": "Nextcloud", "version": "32.0.1", "a_jour": True, "images": [], "lu": 1}
    }
    assert alerts == []
    tool_updates.check_all(now=2, get=Registry({"32": "sha256:neuf"}))
    assert len(alerts) == 1 and "Nextcloud" in alerts[0][1]
    tool_updates.check_all(now=3, get=Registry({"32": "sha256:neuf"}))
    assert len(alerts) == 1
    tool_updates.forget("nextcloud")
    assert tool_updates.summary()["outils"]["nextcloud"]["a_jour"] is True
    locales["empreintes"] = ["sha256:neuf"]
    etat = tool_updates.check_all(now=4, get=Registry({"32": "sha256:neuf"}))
    assert etat["outils"]["nextcloud"]["a_jour"] is True and len(alerts) == 1


def test_unreachable_registry_suggests_nothing(serveur):
    _, alerts = serveur
    import httpx

    def failure(*a, **k):
        raise httpx.ConnectError("hors ligne")

    etat = tool_updates.check_all(now=1, get=failure)
    assert etat["outils"]["nextcloud"]["a_jour"] is True and alerts == []
