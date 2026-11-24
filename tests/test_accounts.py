"""Mot de passe oublié : le mot de passe provisoire ne va qu'au compte Tailscale relié (revue, point 4)."""

import pytest

from security import accounts


@pytest.fixture(autouse=True)
def new_file(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", str(tmp_path / "comptes.json"))
    monkeypatch.setattr(accounts, "DATA_DIR", str(tmp_path))
    accounts._pending_resets.clear()


def create_item(identifier, rank, tailscale):
    c, _ = accounts.create(identifier, identifier.capitalize(), rank, tailscale, "test", "super", set())
    accounts._pending_resets.clear()
    return c


def test_request_from_the_right_tailscale_account():
    create_item("chef", "super", "chef@exemple.fr")
    c = accounts.request_reset("chef", "chef@exemple.fr", "Chef")
    assert c and c["identifiant"] == "chef"
    req = accounts._read()["demandes"][-1]
    _, password = accounts.answer_reset(req["id"], True, "autre-super")
    assert password and req["id"]
    assert accounts.collect_reset("chef@exemple.fr")["mot_de_passe"] == password


def test_request_from_another_tailscale_account_ignored():
    create_item("chef", "super", "chef@exemple.fr")
    assert accounts.request_reset("chef", "pirate@exemple.fr", "Pirate") is None
    assert accounts._read()["demandes"] == []


def test_account_without_tailscale_no_request():
    """Le cas de la revue : compte non relié, le provisoire serait parti chez le demandeur."""
    create_item("chef", "super", None)
    assert accounts.request_reset("chef", "admin-malin@exemple.fr", "Admin") is None
    assert accounts._read()["demandes"] == []
    assert not accounts.pending_reset("admin-malin@exemple.fr")


def test_unknown_login():
    assert accounts.request_reset("personne", "x@exemple.fr", "X") is None


def test_reset_never_goes_to_someone_else_even_if_the_link_changes():
    """Le lien Tailscale est retiré entre la demande et l'acceptation : rien n'est remis."""
    c = create_item("chef", "super", "chef@exemple.fr")
    accounts.request_reset("chef", "chef@exemple.fr", "Chef")
    accounts.update(c["id"], "super", tailscale=None)
    req = accounts._read()["demandes"][-1]
    with pytest.raises(accounts.Refusal):
        accounts.answer_reset(req["id"], True, "autre-super")
    assert not accounts.pending_reset("chef@exemple.fr")


def test_reset_only_once():
    create_item("chef", "super", "chef@exemple.fr")
    accounts.request_reset("chef", "chef@exemple.fr", "Chef")
    req = accounts._read()["demandes"][-1]
    accounts.answer_reset(req["id"], True, "autre-super")
    assert accounts.collect_reset("chef@exemple.fr")
    assert accounts.collect_reset("chef@exemple.fr") is None
