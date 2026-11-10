"""Mot de passe oublié : le mot de passe provisoire ne va qu'au compte Tailscale relié (revue, point 4)."""
import pytest

import comptes


@pytest.fixture(autouse=True)
def fichier_neuf(tmp_path, monkeypatch):
    monkeypatch.setattr(comptes, "FICHIER", str(tmp_path / "comptes.json"))
    monkeypatch.setattr(comptes, "DOSSIER", str(tmp_path))
    comptes._remises.clear()


def creer(ident, rang, tailscale):
    c, _ = comptes.creer(ident, ident.capitalize(), rang, tailscale, "test", "super", set())
    comptes._remises.clear()
    return c


def test_demande_depuis_le_bon_compte_tailscale():
    creer("chef", "super", "chef@exemple.fr")
    c = comptes.demander("chef", "chef@exemple.fr", "Chef")
    assert c and c["identifiant"] == "chef"
    dem = comptes._lire()["demandes"][-1]
    _, mdp = comptes.repondre(dem["id"], True, "autre-super")
    assert mdp and dem["id"]
    assert comptes.prendre_remise("chef@exemple.fr")["mot_de_passe"] == mdp


def test_demande_depuis_un_autre_compte_tailscale_ignoree():
    creer("chef", "super", "chef@exemple.fr")
    assert comptes.demander("chef", "pirate@exemple.fr", "Pirate") is None
    assert comptes._lire()["demandes"] == []


def test_compte_sans_tailscale_aucune_demande():
    """Le cas de la revue : compte non relié, le provisoire serait parti chez le demandeur."""
    creer("chef", "super", None)
    assert comptes.demander("chef", "admin-malin@exemple.fr", "Admin") is None
    assert comptes._lire()["demandes"] == []
    assert not comptes.remise_en_attente("admin-malin@exemple.fr")


def test_identifiant_inconnu():
    assert comptes.demander("personne", "x@exemple.fr", "X") is None


def test_remise_jamais_a_quelqu_un_d_autre_meme_si_le_lien_change():
    """Le lien Tailscale est retiré entre la demande et l'acceptation : rien n'est remis."""
    c = creer("chef", "super", "chef@exemple.fr")
    comptes.demander("chef", "chef@exemple.fr", "Chef")
    comptes.modifier(c["id"], "super", tailscale=None)
    dem = comptes._lire()["demandes"][-1]
    with pytest.raises(comptes.Refus):
        comptes.repondre(dem["id"], True, "autre-super")
    assert not comptes.remise_en_attente("chef@exemple.fr")


def test_remise_une_seule_fois():
    creer("chef", "super", "chef@exemple.fr")
    comptes.demander("chef", "chef@exemple.fr", "Chef")
    dem = comptes._lire()["demandes"][-1]
    comptes.repondre(dem["id"], True, "autre-super")
    assert comptes.prendre_remise("chef@exemple.fr")
    assert comptes.prendre_remise("chef@exemple.fr") is None
