"""Mot de passe oublié : le mot de passe provisoire ne va qu'au compte Tailscale relié (revue, point 4)."""
import pytest

from security import accounts


@pytest.fixture(autouse=True)
def fichier_neuf(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "FICHIER", str(tmp_path / "comptes.json"))
    monkeypatch.setattr(accounts, "DOSSIER", str(tmp_path))
    accounts._remises.clear()


def creer(ident, rang, tailscale):
    c, _ = accounts.creer(ident, ident.capitalize(), rang, tailscale, "test", "super", set())
    accounts._remises.clear()
    return c


def test_demande_depuis_le_bon_compte_tailscale():
    creer("chef", "super", "chef@exemple.fr")
    c = accounts.demander("chef", "chef@exemple.fr", "Chef")
    assert c and c["identifiant"] == "chef"
    dem = accounts._lire()["demandes"][-1]
    _, mdp = accounts.repondre(dem["id"], True, "autre-super")
    assert mdp and dem["id"]
    assert accounts.prendre_remise("chef@exemple.fr")["mot_de_passe"] == mdp


def test_demande_depuis_un_autre_compte_tailscale_ignoree():
    creer("chef", "super", "chef@exemple.fr")
    assert accounts.demander("chef", "pirate@exemple.fr", "Pirate") is None
    assert accounts._lire()["demandes"] == []


def test_compte_sans_tailscale_aucune_demande():
    """Le cas de la revue : compte non relié, le provisoire serait parti chez le demandeur."""
    creer("chef", "super", None)
    assert accounts.demander("chef", "admin-malin@exemple.fr", "Admin") is None
    assert accounts._lire()["demandes"] == []
    assert not accounts.remise_en_attente("admin-malin@exemple.fr")


def test_identifiant_inconnu():
    assert accounts.demander("personne", "x@exemple.fr", "X") is None


def test_remise_jamais_a_quelqu_un_d_autre_meme_si_le_lien_change():
    """Le lien Tailscale est retiré entre la demande et l'acceptation : rien n'est remis."""
    c = creer("chef", "super", "chef@exemple.fr")
    accounts.demander("chef", "chef@exemple.fr", "Chef")
    accounts.modifier(c["id"], "super", tailscale=None)
    dem = accounts._lire()["demandes"][-1]
    with pytest.raises(accounts.Refus):
        accounts.repondre(dem["id"], True, "autre-super")
    assert not accounts.remise_en_attente("chef@exemple.fr")


def test_remise_une_seule_fois():
    creer("chef", "super", "chef@exemple.fr")
    accounts.demander("chef", "chef@exemple.fr", "Chef")
    dem = accounts._lire()["demandes"][-1]
    accounts.repondre(dem["id"], True, "autre-super")
    assert accounts.prendre_remise("chef@exemple.fr")
    assert accounts.prendre_remise("chef@exemple.fr") is None
