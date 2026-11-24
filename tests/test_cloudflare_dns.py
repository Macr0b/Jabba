"""L'entrée DNS réglée par Jabba chez Cloudflare : jeton limité au domaine, seule la ligne « cloud » touchée,
sans proxy ; la clé OVH oubliée quand le DNS a déménagé."""

import json
import urllib.parse

import httpx
import pytest

from services import cloudflare_dns, dns_update, ovh_dns

TOKEN = "J" * 40


class FakeCloudflare:
    def __init__(self, zones=("exemple.fr",)):
        self.zones = [{"id": f"z{i}", "name": nom} for i, nom in enumerate(zones)]
        self.entries = {}
        self.suivant = 1
        self.calls = []
        self.refused = False

    def add_item(self, type_, nom, content, proxied=False):
        self.entries[str(self.suivant)] = {
            "id": str(self.suivant),
            "type": type_,
            "name": nom,
            "content": content,
            "proxied": proxied,
        }
        self.suivant += 1

    def __call__(self, method, url, json=None, params=None, headers=None, timeout=None):
        chemin = urllib.parse.urlsplit(url).path.removeprefix("/client/v4")
        self.calls.append((method, chemin, json, params, headers))
        if self.refused:
            return httpx.Response(403, json={"success": False, "errors": [{"code": 9109}]})
        if chemin == "/zones":
            return httpx.Response(200, json={"success": True, "result": self.zones})
        if method == "GET" and chemin.endswith("/dns_records"):
            found_ones = [
                e for e in self.entries.values() if e["type"] == params["type"] and e["name"] == params["name"]
            ]
            return httpx.Response(200, json={"success": True, "result": found_ones})
        if method == "PATCH":
            self.entries[chemin.rsplit("/", 1)[1]].update(json)
            return httpx.Response(200, json={"success": True, "result": {}})
        if method == "POST":
            self.add_item(json["type"], json["name"], json["content"], json["proxied"])
            return httpx.Response(200, json={"success": True, "result": {}})
        return httpx.Response(404, json={"success": False})


@pytest.fixture
def cloudflare(tmp_path, monkeypatch):
    monkeypatch.setattr(cloudflare_dns, "KEYS_FILE", str(tmp_path / "cloudflare.json"))
    monkeypatch.setattr(ovh_dns, "KEYS_FILE", str(tmp_path / "ovh.json"))
    monkeypatch.setattr(cloudflare_dns.activity_log, "record", lambda *a, **k: None)
    fake = FakeCloudflare()
    monkeypatch.setattr(cloudflare_dns.httpx, "request", fake)
    return fake


def test_token_kept_after_check(cloudflare, tmp_path):
    cloudflare_dns.save_token("exemple.fr", f"  {TOKEN} ")
    assert cloudflare_dns.is_ready()
    kept = json.loads((tmp_path / "cloudflare.json").read_text())
    assert kept == {"token": TOKEN, "zone": "exemple.fr", "zone_id": "z0"}
    assert oct((tmp_path / "cloudflare.json").stat().st_mode)[-3:] == "600"
    assert all(call[4]["Authorization"] == f"Bearer {TOKEN}" for call in cloudflare.calls)
    assert not [a for a in cloudflare.calls if a[0] != "GET"]  # vérifier n'écrit rien


@pytest.mark.parametrize(
    "zones, message",
    [
        (("autre.fr",), "n'a pas accès à exemple.fr"),
        (("exemple.fr", "autre-projet.fr"), "limite-le à exemple.fr seul"),  # jeton trop large : refusé
    ],
)
def test_token_too_broad_or_wrong_domain_refused(cloudflare, monkeypatch, zones, message):
    monkeypatch.setattr(cloudflare_dns.httpx, "request", FakeCloudflare(zones))
    with pytest.raises(cloudflare_dns.CloudflareError, match=message):
        cloudflare_dns.save_token("exemple.fr", TOKEN)
    assert not cloudflare_dns.is_ready()


def test_token_miscopied_or_refused(cloudflare):
    with pytest.raises(cloudflare_dns.CloudflareError, match="recopie-le"):
        cloudflare_dns.save_token("exemple.fr", "trop-court")
    assert cloudflare.calls == []
    cloudflare.refused = True
    with pytest.raises(cloudflare_dns.CloudflareError, match="Modifier la zone DNS"):
        cloudflare_dns.save_token("exemple.fr", TOKEN)
    assert not cloudflare_dns.is_ready()


def test_only_the_cloud_line_is_touched(cloudflare):
    cloudflare.add_item("A", "exemple.fr", "192.0.2.1")  # le site d'un autre projet
    cloudflare.add_item("A", "www.exemple.fr", "192.0.2.1")
    cloudflare_dns.save_token("exemple.fr", TOKEN)
    assert cloudflare_dns.sync("exemple.fr", "cloud", "203.0.113.7") == "cree"
    created = cloudflare.entries["3"]
    assert created["name"] == "cloud.exemple.fr" and created["proxied"] is False  # nuage gris
    assert cloudflare_dns.sync("exemple.fr", "cloud", "203.0.113.7") == "deja"
    assert cloudflare_dns.sync("exemple.fr", "cloud", "198.51.100.4") == "corrige"
    assert cloudflare.entries["3"]["content"] == "198.51.100.4"
    assert cloudflare.entries["1"]["content"] == cloudflare.entries["2"]["content"] == "192.0.2.1"
    writes = [a for a in cloudflare.calls if a[0] != "GET"]
    assert [a[0] for a in writes] == ["POST", "PATCH"] and writes[1][2] == {"content": "198.51.100.4"}


def test_existing_proxy_left_as_is(cloudflare):
    cloudflare.add_item("A", "cloud.exemple.fr", "192.0.2.9", proxied=True)
    cloudflare_dns.save_token("exemple.fr", TOKEN)
    cloudflare_dns.sync("exemple.fr", "cloud", "203.0.113.7")
    assert cloudflare.entries["1"]["proxied"] is True  # Jabba change l'adresse, rien d'autre


def test_cname_never_deleted(cloudflare):
    cloudflare.add_item("CNAME", "cloud.exemple.fr", "ailleurs.exemple.net")
    cloudflare_dns.save_token("exemple.fr", TOKEN)
    with pytest.raises(cloudflare_dns.CloudflareError, match="CNAME"):
        cloudflare_dns.sync("exemple.fr", "cloud", "203.0.113.7")
    assert not [a for a in cloudflare.calls if a[0] != "GET"]


def test_token_of_another_domain(cloudflare):
    cloudflare_dns.save_token("exemple.fr", TOKEN)
    with pytest.raises(cloudflare_dns.CloudflareError, match="colle celui de autre.fr"):
        cloudflare_dns.sync("autre.fr", "cloud", "203.0.113.7")


def test_cloudflare_comes_before_ovh(cloudflare, monkeypatch):
    assert dns_update.provider() is None
    ovh_dns.json_files.save(ovh_dns.KEYS_FILE, {"ak": "a", "as": "b", "ck": "c"}, private=True)
    assert dns_update.provider() == "ovh"
    cloudflare_dns.save_token("exemple.fr", TOKEN)
    assert dns_update.provider() == "cloudflare"
    from services import domain_access

    monkeypatch.setattr(domain_access, "public_ip", lambda: "203.0.113.7")
    ovh_calls = []
    monkeypatch.setattr(ovh_dns, "sync", lambda *a: ovh_calls.append(a))
    assert dns_update.sync_now({"domain": "exemple.fr", "prefix": "cloud", "active": True}) == "cree"
    assert ovh_calls == []  # l'ancienne mise à jour OVH ne tourne plus
    assert dns_update.sync_now({"domain": "exemple.fr", "active": False}) is None


def test_saving_the_token_drops_the_ovh_key(cloudflare, monkeypatch):
    """Route : le jeton gardé, la clé OVH oubliée, l'entrée réglée, le Journal tenu."""
    from fastapi.testclient import TestClient

    import main
    from routes import domain_access as route
    from security import guard

    ovh_dns.json_files.save(ovh_dns.KEYS_FILE, {"ak": "a", "as": "b", "ck": "c"}, private=True)
    log = []
    monkeypatch.setattr(route.activity_log, "record", lambda famille, texte, *a: log.append(texte))
    monkeypatch.setattr(guard, "require_super_admin", lambda request: {"username": "serveur-test"})
    monkeypatch.setattr(
        route.domain_access, "read", lambda: {"domain": "exemple.fr", "prefix": "cloud", "active": True}
    )
    monkeypatch.setattr(route.domain_access, "public_ip", lambda: "203.0.113.7")
    monkeypatch.setattr(route.domain_access, "status", lambda *a: {"dns_auto": dns_update.provider()})
    with TestClient(main.app, base_url="https://testserver") as client:
        client.post(
            "/api/admin/connexion",
            json={"username": "serveur-demo", "password": "demo-mot-de-passe"},
            headers={"X-Jabba": "1"},
        )
        answer = client.post("/api/domaine/cloudflare", json={"token": TOKEN}, headers={"X-Jabba": "1"})
    assert answer.status_code == 200 and answer.json() == {"dns_auto": "cloudflare"}
    assert not ovh_dns.is_ready() and any("Clé OVH oubliée" in line for line in log)
    assert cloudflare.entries["1"]["name"] == "cloud.exemple.fr"
