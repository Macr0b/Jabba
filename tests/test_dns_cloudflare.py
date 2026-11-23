"""L'entrée DNS réglée par Jabba chez Cloudflare : jeton limité au domaine, seule la ligne « cloud » touchée,
sans proxy ; la clé OVH oubliée quand le DNS a déménagé."""

import json
import urllib.parse

import httpx
import pytest

from services import cloudflare_dns, dns_update, ovh_dns

JETON = "J" * 40


class FauxCloudflare:
    def __init__(self, zones=("exemple.fr",)):
        self.zones = [{"id": f"z{i}", "name": nom} for i, nom in enumerate(zones)]
        self.entrees = {}
        self.suivant = 1
        self.appels = []
        self.refuse = False

    def ajouter(self, type_, nom, contenu, proxied=False):
        self.entrees[str(self.suivant)] = {
            "id": str(self.suivant),
            "type": type_,
            "name": nom,
            "content": contenu,
            "proxied": proxied,
        }
        self.suivant += 1

    def __call__(self, methode, url, json=None, params=None, headers=None, timeout=None):
        chemin = urllib.parse.urlsplit(url).path.removeprefix("/client/v4")
        self.appels.append((methode, chemin, json, params, headers))
        if self.refuse:
            return httpx.Response(403, json={"success": False, "errors": [{"code": 9109}]})
        if chemin == "/zones":
            return httpx.Response(200, json={"success": True, "result": self.zones})
        if methode == "GET" and chemin.endswith("/dns_records"):
            trouvees = [e for e in self.entrees.values() if e["type"] == params["type"] and e["name"] == params["name"]]
            return httpx.Response(200, json={"success": True, "result": trouvees})
        if methode == "PATCH":
            self.entrees[chemin.rsplit("/", 1)[1]].update(json)
            return httpx.Response(200, json={"success": True, "result": {}})
        if methode == "POST":
            self.ajouter(json["type"], json["name"], json["content"], json["proxied"])
            return httpx.Response(200, json={"success": True, "result": {}})
        return httpx.Response(404, json={"success": False})


@pytest.fixture
def cloudflare(tmp_path, monkeypatch):
    monkeypatch.setattr(cloudflare_dns, "KEYS_FILE", str(tmp_path / "cloudflare.json"))
    monkeypatch.setattr(ovh_dns, "KEYS_FILE", str(tmp_path / "ovh.json"))
    monkeypatch.setattr(cloudflare_dns.activity_log, "record", lambda *a, **k: None)
    faux = FauxCloudflare()
    monkeypatch.setattr(cloudflare_dns.httpx, "request", faux)
    return faux


def test_jeton_garde_apres_verification(cloudflare, tmp_path):
    cloudflare_dns.save_token("exemple.fr", f"  {JETON} ")
    assert cloudflare_dns.is_ready()
    garde = json.loads((tmp_path / "cloudflare.json").read_text())
    assert garde == {"jeton": JETON, "zone": "exemple.fr", "zone_id": "z0"}
    assert oct((tmp_path / "cloudflare.json").stat().st_mode)[-3:] == "600"
    assert all(appel[4]["Authorization"] == f"Bearer {JETON}" for appel in cloudflare.appels)
    assert not [a for a in cloudflare.appels if a[0] != "GET"]  # vérifier n'écrit rien


@pytest.mark.parametrize(
    "zones, message",
    [
        (("autre.fr",), "n'a pas accès à exemple.fr"),
        (("exemple.fr", "autre-projet.fr"), "limite-le à exemple.fr seul"),  # jeton trop large : refusé
    ],
)
def test_jeton_trop_large_ou_mauvais_domaine_refuse(cloudflare, monkeypatch, zones, message):
    monkeypatch.setattr(cloudflare_dns.httpx, "request", FauxCloudflare(zones))
    with pytest.raises(cloudflare_dns.CloudflareError, match=message):
        cloudflare_dns.save_token("exemple.fr", JETON)
    assert not cloudflare_dns.is_ready()


def test_jeton_mal_recopie_ou_refuse(cloudflare):
    with pytest.raises(cloudflare_dns.CloudflareError, match="recopie-le"):
        cloudflare_dns.save_token("exemple.fr", "trop-court")
    assert cloudflare.appels == []
    cloudflare.refuse = True
    with pytest.raises(cloudflare_dns.CloudflareError, match="Modifier la zone DNS"):
        cloudflare_dns.save_token("exemple.fr", JETON)
    assert not cloudflare_dns.is_ready()


def test_seule_la_ligne_cloud_est_touchee(cloudflare):
    cloudflare.ajouter("A", "exemple.fr", "192.0.2.1")  # le site d'un autre projet
    cloudflare.ajouter("A", "www.exemple.fr", "192.0.2.1")
    cloudflare_dns.save_token("exemple.fr", JETON)
    assert cloudflare_dns.sync("exemple.fr", "cloud", "203.0.113.7") == "cree"
    creee = cloudflare.entrees["3"]
    assert creee["name"] == "cloud.exemple.fr" and creee["proxied"] is False  # nuage gris
    assert cloudflare_dns.sync("exemple.fr", "cloud", "203.0.113.7") == "deja"
    assert cloudflare_dns.sync("exemple.fr", "cloud", "198.51.100.4") == "corrige"
    assert cloudflare.entrees["3"]["content"] == "198.51.100.4"
    assert cloudflare.entrees["1"]["content"] == cloudflare.entrees["2"]["content"] == "192.0.2.1"
    ecritures = [a for a in cloudflare.appels if a[0] != "GET"]
    assert [a[0] for a in ecritures] == ["POST", "PATCH"] and ecritures[1][2] == {"content": "198.51.100.4"}


def test_proxy_existant_laisse_tel_quel(cloudflare):
    cloudflare.ajouter("A", "cloud.exemple.fr", "192.0.2.9", proxied=True)
    cloudflare_dns.save_token("exemple.fr", JETON)
    cloudflare_dns.sync("exemple.fr", "cloud", "203.0.113.7")
    assert cloudflare.entrees["1"]["proxied"] is True  # Jabba change l'adresse, rien d'autre


def test_cname_jamais_efface(cloudflare):
    cloudflare.ajouter("CNAME", "cloud.exemple.fr", "ailleurs.exemple.net")
    cloudflare_dns.save_token("exemple.fr", JETON)
    with pytest.raises(cloudflare_dns.CloudflareError, match="CNAME"):
        cloudflare_dns.sync("exemple.fr", "cloud", "203.0.113.7")
    assert not [a for a in cloudflare.appels if a[0] != "GET"]


def test_jeton_d_un_autre_domaine(cloudflare):
    cloudflare_dns.save_token("exemple.fr", JETON)
    with pytest.raises(cloudflare_dns.CloudflareError, match="colle celui de autre.fr"):
        cloudflare_dns.sync("autre.fr", "cloud", "203.0.113.7")


def test_cloudflare_passe_avant_ovh(cloudflare, monkeypatch):
    assert dns_update.provider() is None
    ovh_dns.json_files.save(ovh_dns.KEYS_FILE, {"ak": "a", "as": "b", "ck": "c"}, private=True)
    assert dns_update.provider() == "ovh"
    cloudflare_dns.save_token("exemple.fr", JETON)
    assert dns_update.provider() == "cloudflare"
    from services import domain_access

    monkeypatch.setattr(domain_access, "public_ip", lambda: "203.0.113.7")
    appels_ovh = []
    monkeypatch.setattr(ovh_dns, "sync", lambda *a: appels_ovh.append(a))
    assert dns_update.sync_now({"domaine": "exemple.fr", "prefixe": "cloud", "actif": True}) == "cree"
    assert appels_ovh == []  # l'ancienne mise à jour OVH ne tourne plus
    assert dns_update.sync_now({"domaine": "exemple.fr", "actif": False}) is None


def test_enregistrer_le_jeton_coupe_la_cle_ovh(cloudflare, monkeypatch):
    """Route : le jeton gardé, la clé OVH oubliée, l'entrée réglée, le Journal tenu."""
    from fastapi.testclient import TestClient

    import main
    from routes import domain_access as route
    from security import guard

    ovh_dns.json_files.save(ovh_dns.KEYS_FILE, {"ak": "a", "as": "b", "ck": "c"}, private=True)
    journal = []
    monkeypatch.setattr(route.activity_log, "record", lambda famille, texte, *a: journal.append(texte))
    monkeypatch.setattr(guard, "require_super_admin", lambda request: {"identifiant": "serveur-test"})
    monkeypatch.setattr(
        route.domain_access, "read", lambda: {"domaine": "exemple.fr", "prefixe": "cloud", "actif": True}
    )
    monkeypatch.setattr(route.domain_access, "public_ip", lambda: "203.0.113.7")
    monkeypatch.setattr(route.domain_access, "status", lambda *a: {"dns_auto": dns_update.provider()})
    with TestClient(main.app, base_url="https://testserver") as client:
        client.post(
            "/api/admin/connexion",
            json={"identifiant": "serveur-demo", "mot_de_passe": "demo-mot-de-passe"},
            headers={"X-Jabba": "1"},
        )
        reponse = client.post("/api/domaine/cloudflare", json={"jeton": JETON}, headers={"X-Jabba": "1"})
    assert reponse.status_code == 200 and reponse.json() == {"dns_auto": "cloudflare"}
    assert not ovh_dns.is_ready() and any("Clé OVH oubliée" in ligne for ligne in journal)
    assert cloudflare.entrees["1"]["name"] == "cloud.exemple.fr"
