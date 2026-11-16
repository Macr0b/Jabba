"""Mises à jour des outils : lecture des références d'images, registre (jeton anonyme), alerte une seule fois."""

import json

import pytest

from services import tool_updates


@pytest.mark.parametrize(
    "image, attendu",
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
def test_reference(image, attendu):
    assert tool_updates.parse_reference(image) == attendu


class Reponse:
    def __init__(self, code, entetes=None, corps=None):
        self.status_code = code
        self.headers = {k.lower(): v for k, v in (entetes or {}).items()}
        self._corps = corps or {}

    def json(self):
        return self._corps


class Registre:
    """Un registre qui exige un jeton anonyme, comme Docker Hub."""

    def __init__(self, empreintes):
        self.empreintes = empreintes
        self.appels = []

    def __call__(self, url, params=None, headers=None, method="GET"):
        self.appels.append((method, url, params, dict(headers or {})))
        if url.startswith("https://auth.exemple/token"):
            assert params == {"service": "registry.docker.io", "scope": "repository:library/nextcloud:pull"}
            return Reponse(200, corps={"token": "jeton"})
        if (headers or {}).get("Authorization") != "Bearer jeton":
            return Reponse(
                401,
                {
                    "WWW-Authenticate": 'Bearer realm="https://auth.exemple/token",service="registry.docker.io",scope="repository:library/nextcloud:pull"'
                },
            )
        tag = url.rsplit("/", 1)[-1]
        if tag not in self.empreintes:
            return Reponse(404)
        return Reponse(200, {"Docker-Content-Digest": self.empreintes[tag]})


def test_empreinte_distante_avec_jeton():
    registre = Registre({"32": "sha256:neuf"})
    assert tool_updates.remote_digest("registry-1.docker.io", "library/nextcloud", "32", registre) == "sha256:neuf"
    assert registre.appels[-1][0] == "HEAD" and "manifest.list" in registre.appels[-1][3]["Accept"]
    assert tool_updates.remote_digest("registry-1.docker.io", "library/nextcloud", "absent", registre) is None


def test_royaume_non_https_refuse():
    def get(url, params=None, headers=None, method="GET"):
        return Reponse(401, {"WWW-Authenticate": 'Bearer realm="http://pirate/token"'})

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

    def hote(commande, timeout=30, input_text=None):
        if commande[:2] == ["docker", "inspect"]:
            return 0, "nextcloud:32|sha256:" + "a" * 64 + "\n", ""
        if commande[:3] == ["docker", "image", "inspect"]:
            depots = json.dumps([f"nextcloud@{e}" for e in locales["empreintes"]])
            return 0, f"{depots}|32.0.1\n", ""
        return 1, "", ""

    monkeypatch.setattr(tool_updates.commands, "run_on_host", hote)
    alertes = []
    monkeypatch.setattr(tool_updates.notifications, "send", lambda *a, **k: alertes.append(a))
    return locales, alertes


def test_a_jour_puis_nouvelle_version_une_seule_alerte(serveur):
    locales, alertes = serveur
    etat = tool_updates.check_all(now=1, get=Registre({"32": "sha256:ancien"}))
    assert etat["outils"] == {
        "nextcloud": {"nom": "Nextcloud", "version": "32.0.1", "a_jour": True, "images": [], "lu": 1}
    }
    assert alertes == []
    tool_updates.check_all(now=2, get=Registre({"32": "sha256:neuf"}))
    assert len(alertes) == 1 and "Nextcloud" in alertes[0][1]
    tool_updates.check_all(now=3, get=Registre({"32": "sha256:neuf"}))
    assert len(alertes) == 1
    tool_updates.forget("nextcloud")
    assert tool_updates.summary()["outils"]["nextcloud"]["a_jour"] is True
    locales["empreintes"] = ["sha256:neuf"]
    etat = tool_updates.check_all(now=4, get=Registre({"32": "sha256:neuf"}))
    assert etat["outils"]["nextcloud"]["a_jour"] is True and len(alertes) == 1


def test_registre_injoignable_ne_fait_rien_croire(serveur):
    _, alertes = serveur
    import httpx

    def panne(*a, **k):
        raise httpx.ConnectError("hors ligne")

    etat = tool_updates.check_all(now=1, get=panne)
    assert etat["outils"]["nextcloud"]["a_jour"] is True and alertes == []
