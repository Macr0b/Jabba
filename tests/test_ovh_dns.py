"""L'entrée DNS réglée par Jabba chez OVH : clé limitée à la zone, entrée créée ou corrigée, jamais un CNAME effacé."""

import hashlib
import json
import urllib.parse

import httpx
import pytest

from services import ovh_dns


class FakeOvh:
    def __init__(self):
        self.entries = {}  # id -> entrée
        self.suivant = 1
        self.calls = []
        self.refused = False

    def add_item(self, type_, subs, target):
        self.entries[self.suivant] = {"id": self.suivant, "fieldType": type_, "subDomain": subs, "target": target}
        self.suivant += 1

    def __call__(self, method, url, content=None, headers=None, timeout=None):
        chemin = urllib.parse.urlsplit(url).path.removeprefix("/1.0")
        query_ = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        body = json.loads(content) if content else None
        self.calls.append((method, chemin, body, headers))
        if self.refused:
            return httpx.Response(403, json={"message": "This call has not been granted"})
        if method == "GET" and chemin.endswith("/record"):
            ids = [
                i
                for i, e in self.entries.items()
                if e["fieldType"] == query_["fieldType"][0] and e["subDomain"] == query_["subDomain"][0]
            ]
            return httpx.Response(200, json=ids)
        if method == "GET" and "/record/" in chemin:
            return httpx.Response(200, json=self.entries[int(chemin.rsplit("/", 1)[1])])
        if method == "PUT":
            self.entries[int(chemin.rsplit("/", 1)[1])].update(body)
            return httpx.Response(200, content=b"")
        if method == "POST" and chemin.endswith("/record"):
            self.add_item(body["fieldType"], body["subDomain"], body["target"])
            return httpx.Response(200, json={"id": self.suivant - 1})
        return httpx.Response(200, content=b"")


@pytest.fixture
def ovh(tmp_path, monkeypatch):
    monkeypatch.setattr(ovh_dns, "KEYS_FILE", str(tmp_path / "ovh.json"))
    fake = FakeOvh()
    monkeypatch.setattr(ovh_dns.httpx, "get", lambda url, timeout=None: httpx.Response(200, text="1700000000"))
    monkeypatch.setattr(ovh_dns.httpx, "request", fake)
    monkeypatch.setattr(ovh_dns.activity_log, "record", lambda *a, **k: None)
    return fake


KEYS = ("AppKey12345678ab", "A" * 32, "C" * 32)


def test_creation_link_limited_to_the_zone():
    link = ovh_dns.token_link("exemple.fr")
    rights = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
    assert link.startswith("https://eu.api.ovh.com/createToken/")
    assert rights == {
        "GET": ["/domain/zone/exemple.fr/*"],
        "POST": ["/domain/zone/exemple.fr/*"],
        "PUT": ["/domain/zone/exemple.fr/*"],
    }
    # pas de DELETE : Jabba n'efface jamais rien chez OVH


def test_key_saved_after_a_try(ovh, tmp_path):
    assert not ovh_dns.is_ready()
    ovh_dns.save_keys("exemple.fr", *KEYS)
    assert ovh_dns.is_ready()
    assert (tmp_path / "ovh.json").stat().st_mode & 0o077 == 0  # lisible par l'appli seule
    method, chemin, _, headers_ = ovh.calls[0]
    assert (method, chemin) == ("GET", "/domain/zone/exemple.fr/record")
    url = ovh_dns.API + "/domain/zone/exemple.fr/record?fieldType=A&subDomain=cloud"
    expected = "$1$" + hashlib.sha1(f"{'A' * 32}+{'C' * 32}+GET+{url}++1700000000".encode()).hexdigest()
    assert headers_["X-Ovh-Signature"] == expected and headers_["X-Ovh-Application"] == KEYS[0]


@pytest.mark.parametrize(
    "zone, keys",
    [
        ("", KEYS),
        ("exemple.fr", ("court", "A" * 32, "C" * 32)),
        ("exemple.fr", (KEYS[0], "A" * 32, "C C")),
        ("exemple.fr", (None, "A" * 32, "C" * 32)),
    ],
)
def test_key_refused_before_any_call(ovh, zone, keys):
    with pytest.raises(ovh_dns.OvhError):
        ovh_dns.save_keys(zone, *keys)
    assert not ovh.calls and not ovh_dns.is_ready()


def test_key_refused_by_ovh_is_not_kept(ovh):
    ovh.refused = True
    with pytest.raises(ovh_dns.OvhError, match="OVH refuse la clé"):
        ovh_dns.save_keys("exemple.fr", *KEYS)
    assert not ovh_dns.is_ready()


def test_entry_created_then_nothing_then_fixed(ovh):
    ovh_dns.save_keys("exemple.fr", *KEYS)
    assert ovh_dns.sync("exemple.fr", "cloud", "203.0.113.7") == "cree"
    assert [e for e in ovh.entries.values() if e["fieldType"] == "A"][0]["target"] == "203.0.113.7"
    assert ("POST", "/domain/zone/exemple.fr/refresh", None) in [a[:3] for a in ovh.calls]
    assert ovh_dns.sync("exemple.fr", "cloud", "203.0.113.7") == "deja"
    assert ovh_dns.sync("exemple.fr", "cloud", "198.51.100.4") == "corrige"
    targets = [e["target"] for e in ovh.entries.values()]
    assert targets == ["198.51.100.4"]


def test_two_a_entries_all_fixed(ovh):
    ovh_dns.save_keys("exemple.fr", *KEYS)
    ovh.add_item("A", "cloud", "1.1.1.1")
    ovh.add_item("A", "cloud", "203.0.113.7")
    assert ovh_dns.sync("exemple.fr", "cloud", "203.0.113.7") == "corrige"
    assert {e["target"] for e in ovh.entries.values()} == {"203.0.113.7"}


def test_cname_never_deleted(ovh):
    ovh_dns.save_keys("exemple.fr", *KEYS)
    ovh.add_item("CNAME", "cloud", "ailleurs.exemple.fr.")
    with pytest.raises(ovh_dns.OvhError, match="CNAME"):
        ovh_dns.sync("exemple.fr", "cloud", "203.0.113.7")
    assert len(ovh.entries) == 1 and not [a for a in ovh.calls if a[0] in ("PUT", "POST", "DELETE")]


@pytest.fixture
def watcher(tmp_path, monkeypatch):
    from services import cloudflare_dns, dns_update

    monkeypatch.setattr(cloudflare_dns, "KEYS_FILE", str(tmp_path / "cloudflare.json"))  # pas de jeton Cloudflare
    dns_update._STATE["last"] = 0.0
    return dns_update


def test_hourly_watch(ovh, watcher, monkeypatch):
    from services import domain_access

    ovh_dns.save_keys("exemple.fr", *KEYS)
    monkeypatch.setattr(domain_access, "read", lambda: {"domain": "exemple.fr", "prefix": "cloud", "active": True})
    adresse = {"ip": "203.0.113.7"}
    monkeypatch.setattr(domain_access, "public_ip", lambda: adresse["ip"])
    alerts = []
    monkeypatch.setattr(watcher.notifications, "send", lambda *a, **k: alerts.append(a[1]))
    assert watcher.watch(now=10_000) == "cree"
    assert watcher.watch(now=10_100) is None  # moins d'une heure après : rien
    adresse["ip"] = "198.51.100.4"
    assert watcher.watch(now=20_000) == "corrige"
    assert alerts == ["Domaine : adresse de la box changée"]


def test_no_key_or_inactive_domain_nothing(ovh, watcher, monkeypatch):
    from services import domain_access

    assert watcher.watch(now=10_000) is None  # pas de clé
    ovh_dns.save_keys("exemple.fr", *KEYS)
    monkeypatch.setattr(domain_access, "read", lambda: {"domain": "exemple.fr", "prefix": "cloud", "active": False})
    watcher._STATE["last"] = 0.0
    assert watcher.watch(now=10_000) is None
    assert not [a for a in ovh.calls if a[0] != "GET"]


def test_forget(ovh):
    ovh_dns.save_keys("exemple.fr", *KEYS)
    ovh_dns.forget()
    assert not ovh_dns.is_ready()
    with pytest.raises(ovh_dns.OvhError):
        ovh_dns.sync("exemple.fr", "cloud", "203.0.113.7")
