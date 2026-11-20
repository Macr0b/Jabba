"""L'entrée DNS réglée par Jabba chez OVH : clé limitée à la zone, entrée créée ou corrigée, jamais un CNAME effacé."""

import hashlib
import json
import urllib.parse

import httpx
import pytest

from services import ovh_dns


class FauxOvh:
    def __init__(self):
        self.entrees = {}  # id -> entrée
        self.suivant = 1
        self.appels = []
        self.refuse = False

    def ajouter(self, type_, sous, cible):
        self.entrees[self.suivant] = {"id": self.suivant, "fieldType": type_, "subDomain": sous, "target": cible}
        self.suivant += 1

    def __call__(self, methode, url, content=None, headers=None, timeout=None):
        chemin = urllib.parse.urlsplit(url).path.removeprefix("/1.0")
        requete = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        corps = json.loads(content) if content else None
        self.appels.append((methode, chemin, corps, headers))
        if self.refuse:
            return httpx.Response(403, json={"message": "This call has not been granted"})
        if methode == "GET" and chemin.endswith("/record"):
            ids = [
                i
                for i, e in self.entrees.items()
                if e["fieldType"] == requete["fieldType"][0] and e["subDomain"] == requete["subDomain"][0]
            ]
            return httpx.Response(200, json=ids)
        if methode == "GET" and "/record/" in chemin:
            return httpx.Response(200, json=self.entrees[int(chemin.rsplit("/", 1)[1])])
        if methode == "PUT":
            self.entrees[int(chemin.rsplit("/", 1)[1])].update(corps)
            return httpx.Response(200, content=b"")
        if methode == "POST" and chemin.endswith("/record"):
            self.ajouter(corps["fieldType"], corps["subDomain"], corps["target"])
            return httpx.Response(200, json={"id": self.suivant - 1})
        return httpx.Response(200, content=b"")


@pytest.fixture
def ovh(tmp_path, monkeypatch):
    monkeypatch.setattr(ovh_dns, "KEYS_FILE", str(tmp_path / "ovh.json"))
    faux = FauxOvh()
    monkeypatch.setattr(ovh_dns.httpx, "get", lambda url, timeout=None: httpx.Response(200, text="1700000000"))
    monkeypatch.setattr(ovh_dns.httpx, "request", faux)
    monkeypatch.setattr(ovh_dns.activity_log, "record", lambda *a, **k: None)
    return faux


CLES = ("AppKey12345678ab", "A" * 32, "C" * 32)


def test_lien_de_creation_limite_a_la_zone():
    lien = ovh_dns.token_link("exemple.fr")
    droits = urllib.parse.parse_qs(urllib.parse.urlsplit(lien).query)
    assert lien.startswith("https://eu.api.ovh.com/createToken/")
    assert droits == {
        "GET": ["/domain/zone/exemple.fr/*"],
        "POST": ["/domain/zone/exemple.fr/*"],
        "PUT": ["/domain/zone/exemple.fr/*"],
    }
    # pas de DELETE : Jabba n'efface jamais rien chez OVH


def test_cle_enregistree_apres_essai(ovh, tmp_path):
    assert not ovh_dns.is_ready()
    ovh_dns.save_keys("exemple.fr", *CLES)
    assert ovh_dns.is_ready()
    assert (tmp_path / "ovh.json").stat().st_mode & 0o077 == 0  # lisible par l'appli seule
    methode, chemin, _, entetes = ovh.appels[0]
    assert (methode, chemin) == ("GET", "/domain/zone/exemple.fr/record")
    url = ovh_dns.API + "/domain/zone/exemple.fr/record?fieldType=A&subDomain=cloud"
    attendu = "$1$" + hashlib.sha1(f"{'A' * 32}+{'C' * 32}+GET+{url}++1700000000".encode()).hexdigest()
    assert entetes["X-Ovh-Signature"] == attendu and entetes["X-Ovh-Application"] == CLES[0]


@pytest.mark.parametrize(
    "zone, cles",
    [
        ("", CLES),
        ("exemple.fr", ("court", "A" * 32, "C" * 32)),
        ("exemple.fr", (CLES[0], "A" * 32, "C C")),
        ("exemple.fr", (None, "A" * 32, "C" * 32)),
    ],
)
def test_cle_refusee_avant_tout_appel(ovh, zone, cles):
    with pytest.raises(ovh_dns.OvhError):
        ovh_dns.save_keys(zone, *cles)
    assert not ovh.appels and not ovh_dns.is_ready()


def test_cle_que_ovh_refuse_n_est_pas_gardee(ovh):
    ovh.refuse = True
    with pytest.raises(ovh_dns.OvhError, match="OVH refuse la clé"):
        ovh_dns.save_keys("exemple.fr", *CLES)
    assert not ovh_dns.is_ready()


def test_entree_creee_puis_rien_puis_corrigee(ovh):
    ovh_dns.save_keys("exemple.fr", *CLES)
    assert ovh_dns.sync("exemple.fr", "cloud", "203.0.113.7") == "cree"
    assert [e for e in ovh.entrees.values() if e["fieldType"] == "A"][0]["target"] == "203.0.113.7"
    assert ("POST", "/domain/zone/exemple.fr/refresh", None) in [a[:3] for a in ovh.appels]
    assert ovh_dns.sync("exemple.fr", "cloud", "203.0.113.7") == "deja"
    assert ovh_dns.sync("exemple.fr", "cloud", "198.51.100.4") == "corrige"
    cibles = [e["target"] for e in ovh.entrees.values()]
    assert cibles == ["198.51.100.4"]


def test_deux_entrees_a_toutes_corrigees(ovh):
    ovh_dns.save_keys("exemple.fr", *CLES)
    ovh.ajouter("A", "cloud", "1.1.1.1")
    ovh.ajouter("A", "cloud", "203.0.113.7")
    assert ovh_dns.sync("exemple.fr", "cloud", "203.0.113.7") == "corrige"
    assert {e["target"] for e in ovh.entrees.values()} == {"203.0.113.7"}


def test_cname_jamais_efface(ovh):
    ovh_dns.save_keys("exemple.fr", *CLES)
    ovh.ajouter("CNAME", "cloud", "ailleurs.exemple.fr.")
    with pytest.raises(ovh_dns.OvhError, match="CNAME"):
        ovh_dns.sync("exemple.fr", "cloud", "203.0.113.7")
    assert len(ovh.entrees) == 1 and not [a for a in ovh.appels if a[0] in ("PUT", "POST", "DELETE")]


def test_surveillance_chaque_heure(ovh, monkeypatch):
    from services import domain_access

    ovh_dns.save_keys("exemple.fr", *CLES)
    ovh_dns._STATE["dernier"] = 0.0
    monkeypatch.setattr(domain_access, "read", lambda: {"domaine": "exemple.fr", "prefixe": "cloud", "actif": True})
    adresse = {"ip": "203.0.113.7"}
    monkeypatch.setattr(domain_access, "public_ip", lambda: adresse["ip"])
    alertes = []
    monkeypatch.setattr(ovh_dns.notifications, "send", lambda *a, **k: alertes.append(a[1]))
    assert ovh_dns.watch(now=10_000) == "cree"
    assert ovh_dns.watch(now=10_100) is None  # moins d'une heure après : rien
    adresse["ip"] = "198.51.100.4"
    assert ovh_dns.watch(now=20_000) == "corrige"
    assert alertes == ["Domaine : adresse de la box changée"]


def test_sans_cle_ni_domaine_actif_rien(ovh, monkeypatch):
    from services import domain_access

    ovh_dns._STATE["dernier"] = 0.0
    assert ovh_dns.watch(now=10_000) is None  # pas de clé
    ovh_dns.save_keys("exemple.fr", *CLES)
    monkeypatch.setattr(domain_access, "read", lambda: {"domaine": "exemple.fr", "prefixe": "cloud", "actif": False})
    ovh_dns._STATE["dernier"] = 0.0
    assert ovh_dns.watch(now=10_000) is None
    assert not [a for a in ovh.appels if a[0] != "GET"]


def test_oublier(ovh):
    ovh_dns.save_keys("exemple.fr", *CLES)
    ovh_dns.forget()
    assert not ovh_dns.is_ready()
    with pytest.raises(ovh_dns.OvhError):
        ovh_dns.sync("exemple.fr", "cloud", "203.0.113.7")
