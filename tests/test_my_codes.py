"""« Mes codes » : chaque code existant, son rangement, et jamais sa valeur."""

import pytest

from services import my_codes, tool_credentials


@pytest.fixture
def serveur(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_credentials, "CREDENTIALS_FILE", str(tmp_path / "identifiants.json"))
    monkeypatch.setattr(tool_credentials.config, "DEMO", False)
    monkeypatch.setattr(my_codes.server_password, "recovery_code_exists", lambda: True)
    monkeypatch.setattr(my_codes.second_factor, "summary", lambda identifier: {"active": True, "codes_restants": 7})
    monkeypatch.setattr(my_codes.backups, "read", lambda: {"chemin": None})
    monkeypatch.setattr(my_codes.ovh_dns, "is_ready", lambda: False)
    monkeypatch.setattr(my_codes.tool_addresses, "status", lambda installed: {"cle_prete": False})
    monkeypatch.setattr(my_codes.config, "NC_APP_PASSWORD", "aaaaa-bbbbb-ccccc-ddddd-eeeee")


def by_name(codes):
    return {code["nom"]: code for code in codes}


def test_list_depends_on_what_is_installed(serveur):
    paperless_secret = tool_credentials.password_for("paperless")
    tool_credentials.password_for("time-machine")
    tool_credentials.save("homebox", "rob", "mon-secret-homebox")
    codes = my_codes.inventory({"nextcloud", "paperless", "homebox", "immich", "time-machine", "jabba-app"}, "rob")
    names = by_name(codes)
    assert names["Mot de passe du serveur"]["rangement"] == "trousseau"
    assert "7 codes de secours" in names["Double vérification de Jabba"]["note"]
    assert names["Mot de passe Nextcloud"]["rangement"] == "trousseau"
    assert names["Mot de passe Time Machine (partage Samba)"]["rangement"] == "trousseau"  # le Mac en a besoin
    assert names["Compte Paperless"]["note"].startswith("Créé par Jabba")
    assert names["Compte Homebox"]["note"].startswith("Noté dans Jabba")
    assert names["Compte Immich"]["present"] is False and "Pas encore noté" in names["Compte Immich"]["note"]
    assert names["Mot de passe d'application « Appli Jabba » (Nextcloud)"]["rangement"] == "jabba"
    assert "Phrase de passe des sauvegardes" not in names  # pas de sauvegardes en route
    assert "Clé OVH" not in names
    assert {c["nom"] for c in codes if c["rangement"] == "supprimer"} >= {
        "Mots de passe provisoires (3 mots et un nombre)",
        "Clés d'inscription Tailscale (tskey-auth-…)",
    }
    texte = str(codes)
    for secret in (paperless_secret, "mon-secret-homebox", "aaaaa-bbbbb"):
        assert secret not in texte  # jamais la valeur d'un code


def test_keys_and_backups_when_they_exist(serveur, monkeypatch):
    monkeypatch.setattr(my_codes.backups, "read", lambda: {"chemin": "/srv/disques/R2D2"})
    monkeypatch.setattr(my_codes.backups, "passphrase", lambda: "phrase-secrete")
    monkeypatch.setattr(my_codes.ovh_dns, "is_ready", lambda: True)
    monkeypatch.setattr(my_codes.server_password, "recovery_code_exists", lambda: False)
    names = by_name(my_codes.inventory(set(), "rob"))
    assert names["Phrase de passe des sauvegardes"]["rangement"] == "trousseau"
    assert names["Clé OVH"]["rangement"] == "jabba"
    assert names["Code de secours de Jabba (6 mots)"]["present"] is False
    assert "phrase-secrete" not in str(names)
