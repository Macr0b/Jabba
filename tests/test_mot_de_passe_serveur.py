"""Vérification du mot de passe du serveur (empreintes de /etc/shadow), sans le module « crypt »."""

import subprocess

import pytest
from service_factice import faux_service

from host import actions, system_crypt
from security import server_password


def _empreinte_openssl(mdp, sel, option):
    return subprocess.run(
        ["openssl", "passwd", option, "-salt", sel, mdp], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.mark.parametrize("option", ["-6", "-5", "-1"])
def test_formats_classiques(option):
    """SHA-512, SHA-256 et MD5 : même empreinte que celle calculée par openssl."""
    empreinte = _empreinte_openssl("Mot de passe é 123", "selsel12", option)
    assert system_crypt.hash_with_setting("Mot de passe é 123", empreinte) == empreinte
    assert system_crypt.hash_with_setting("mauvais", empreinte) != empreinte


def test_yescrypt_format_de_debian():
    """yescrypt ($y$), le format de Debian depuis la version 11 : une empreinte se vérifie avec elle-même."""
    reglage = "$y$j9T$F5Jx5fExrKuPp53xLKQ..1$"
    empreinte = system_crypt.hash_with_setting("secret", reglage)
    if empreinte is None:
        pytest.skip("libcrypt sans yescrypt sur cette machine")
    assert empreinte.startswith("$y$")
    assert system_crypt.hash_with_setting("secret", empreinte) == empreinte
    assert system_crypt.hash_with_setting("Secret", empreinte) != empreinte


def test_empreinte_invalide():
    assert system_crypt.hash_with_setting("x", "$9$inconnu$") is None


def test_la_verification_ne_rend_que_oui_ou_non():
    """Le service compare lui-même : l'empreinte de /etc/shadow ne sort pas, le mot de passe ne va pas au serveur."""
    empreinte = _empreinte_openssl("Bon-mot-de-passe", "abcdefgh", "-6")
    lu = (0, f"rob:{empreinte}:19000:0:99999:7:::\n", "")
    nom = "verifier_mot_de_passe_serveur"
    assert actions.host_input(nom, {"utilisateur": "rob"}, "Bon-mot-de-passe") is None
    assert actions.finish(nom, lu, "Bon-mot-de-passe") == (0, "ok", "")
    code, sortie, erreur = actions.finish(nom, lu, "Mauvais")
    assert code == 1 and empreinte not in sortie + erreur
    assert actions.finish(nom, (0, "rob:!:19000::::::\n", ""), "Bon-mot-de-passe")[0] == 2
    assert actions.finish(nom, (5, "", "Compte refusé"), "x")[0] == 5


def test_verification_par_le_serveur(monkeypatch):
    empreinte = _empreinte_openssl("Bon-mot-de-passe", "abcdefgh", "-6")
    vu = []

    def faux_hote(commande, timeout=30, input_text=None):
        vu.append((commande, input_text))
        return 0, f"rob:{empreinte}:19000:0:99999:7:::\n", ""

    faux_service(monkeypatch, faux_hote)
    monkeypatch.setattr(server_password, "_failures", {})
    assert server_password.check_password("rob", "Bon-mot-de-passe")
    assert not server_password.check_password("rob", "Mauvais")
    assert all(entree is None for _, entree in vu)  # le mot de passe n'est jamais envoyé au serveur
    assert server_password._failures and len(next(iter(server_password._failures.values()))) == 1
    assert not server_password.check_password("root; reboot", "Bon-mot-de-passe")  # compte refusé, rien lancé
    assert len(vu) == 2


def test_seul_un_compte_de_personne(tmp_path):
    """root ou un compte de service ne peuvent être ni vérifiés ni changés."""
    commande, _ = actions.build("changer_mot_de_passe_serveur", {"utilisateur": "root"})
    sortie = subprocess.run(
        ["sh", "-c", commande[2].replace("exec chpasswd", "echo change"), *commande[3:]], capture_output=True, text=True
    )
    assert sortie.returncode == 5 and "change" not in sortie.stdout
    for compte in ("Root", "-x", "a b", "rob:x"):
        with pytest.raises(actions.ActionError):
            actions.build("verifier_mot_de_passe_serveur", {"utilisateur": compte})


def test_changement_par_le_service(monkeypatch):
    vu = []
    faux_service(
        monkeypatch,
        lambda commande, timeout=30, input_text=None: vu.append((commande, input_text)) or (0, "", ""),
    )
    monkeypatch.setenv("JABBA_VERIF_FUITES", "0")
    server_password.change_password("rob", "Une phrase de passe bien longue")
    commande, entree = vu[0]
    assert commande[:2] == ["sh", "-c"] and "chpasswd" in commande[2] and commande[-1] == "rob"
    assert entree == "rob:Une phrase de passe bien longue\n"
    assert "Une phrase" not in " ".join(commande)  # jamais dans la liste des processus
    with pytest.raises(actions.ActionError):
        actions.host_input("changer_mot_de_passe_serveur", {"utilisateur": "rob"}, "a\nroot:x")


def test_blocage_par_personne(monkeypatch):
    """5 erreurs bloquent celui qui se trompe pendant 10 minutes, pas les autres admins."""
    monkeypatch.setattr(server_password, "_failures", {})
    jeton = server_password.requester.set("curieux@exemple.fr")
    try:
        for _ in range(server_password.MAX_ATTEMPTS):
            assert not server_password.is_locked()
            server_password.record_failure()
        assert server_password.is_locked()
    finally:
        server_password.requester.reset(jeton)
    jeton = server_password.requester.set("rob@exemple.fr")
    try:
        assert not server_password.is_locked()
    finally:
        server_password.requester.reset(jeton)
