"""Liste fermée des actions sur le serveur (point 1 de la revue) : rien d'autre ne peut être lancé."""

import json
import os
import socket
import threading
import time

import pytest

from host import actions, commands, host_service


def test_actions_connues():
    assert actions.build("nom_du_serveur") == (["hostname"], 10)
    assert actions.build("ejecter", {"chemin": "/srv/disques/R2-D2 (copie) l'été"})[0] == [
        "sh",
        "-c",
        'sync && umount "$1"',
        "ejecter",
        "/srv/disques/R2-D2 (copie) l'été",
    ]
    assert actions.build("sante_disque", {"appareil": "/dev/nvme0n1", "pont_sat": True})[0][-3:] == [
        "-d",
        "sat",
        "/dev/nvme0n1",
    ]
    assert actions.build("espace_disques", {"chemins": ["/", "/srv/data", "/mnt/x"]})[0][-3:] == [
        "/",
        "/srv/data",
        "/mnt/x",
    ]


@pytest.mark.parametrize(
    "nom, args",
    [
        ("rm", {}),
        ("sh", {"commande": "id"}),
        ("", {}),
        ("ejecter", {"chemin": "/etc"}),
        ("ejecter", {"chemin": "/srv/../etc"}),
        ("ejecter", {"chemin": "/srv/disques/x/../../../etc"}),
        ("ejecter", {"chemin": "srv/disques/x"}),
        ("ejecter", {"chemin": "/srv/disques/x\n; reboot"}),
        ("ejecter", {"chemin": "/srv//x"}),
        ("ejecter", {}),
        ("est_monte", {"chemin": "/"}),
        ("sante_disque", {"appareil": "/dev/sda; reboot"}),
        ("sante_disque", {"appareil": "/etc/passwd"}),
        ("sante_disque", {"appareil": "/dev/zram0"}),
        ("espace_disques", {"chemins": "/"}),
        ("espace_disques", {"chemins": []}),
        ("espace_disques", {"chemins": ["/home"]}),
        ("espace_disques", {"chemins": ["/srv/x"] * 33}),
        ("nom_du_serveur", ["liste"]),
    ],
)
def test_tout_le_reste_est_refuse(nom, args):
    with pytest.raises(actions.ActionError):
        actions.build(nom, args)


def test_le_service_ne_lance_que_la_liste():
    lance = []

    def faux(commande, delai):
        lance.append(commande)
        return 0, "serveur-test\n", ""

    assert host_service.handle(json.dumps({"action": "nom_du_serveur"}), faux) == {
        "code": 0,
        "sortie": "serveur-test\n",
        "erreur": "",
    }
    for demande in [
        '{"action": "rm", "args": {}}',
        "pas du json",
        "{}",
        '{"action": "ejecter", "args": {"chemin": "/etc"}}',
        "[]",
    ]:
        reponse = host_service.handle(demande, faux)
        assert reponse["code"] == host_service.REFUSED
    assert lance == [["hostname"]]


def test_par_la_prise_du_service(monkeypatch):
    """De bout en bout : l'appli demande, le service répond par la prise Unix."""
    import tempfile

    chemin = os.path.join(tempfile.mkdtemp(dir="/tmp"), "h.sock")  # chemin court : 108 caractères au plus
    monkeypatch.setattr(host_service, "SOCKET_PATH", chemin)
    monkeypatch.setattr(host_service, "run", lambda commande, delai: (0, " ".join(commande), ""))
    import asyncio

    boucle = asyncio.new_event_loop()

    def servir():
        try:
            boucle.run_until_complete(host_service.main())
        except asyncio.CancelledError:
            pass

    fil = threading.Thread(target=servir, daemon=True)
    fil.start()
    for _ in range(100):
        if os.path.exists(chemin):
            break
        time.sleep(0.02)
    assert oct(os.stat(chemin).st_mode)[-3:] == "660"
    monkeypatch.setattr(commands, "HOST_SOCKET", chemin)
    monkeypatch.setattr(commands, "run_on_host", lambda *a, **k: pytest.fail("l'ancienne voie ne doit pas servir"))
    assert commands.run_action("est_monte", chemin="/srv/disques/R2") == (0, "mountpoint -q /srv/disques/R2", "")
    with pytest.raises(actions.ActionError):
        commands.run_action("ejecter", chemin="/etc")  # refusé avant même d'être envoyé
    # une demande trafiquée envoyée directement à la prise est refusée aussi
    with socket.socket(socket.AF_UNIX) as brut:
        brut.connect(chemin)
        brut.sendall(b'{"action": "sh", "args": {"c": "id"}}\n')
        assert json.loads(brut.recv(4096))["code"] == host_service.REFUSED
    with socket.socket(socket.AF_UNIX) as brut:
        brut.connect(chemin)
        brut.sendall(b"x" * (host_service.MAX_REQUEST_BYTES + 10) + b"\n")
        assert json.loads(brut.recv(4096))["code"] == host_service.REFUSED
    boucle.call_soon_threadsafe(lambda: [tache.cancel() for tache in asyncio.all_tasks(boucle)])
    fil.join(5)


def test_sans_service_l_ancienne_voie_avec_la_meme_commande(monkeypatch):
    monkeypatch.setattr(commands, "HOST_SOCKET", "/nulle/part.sock")
    vu = []
    monkeypatch.setattr(
        commands, "run_on_host", lambda commande, timeout=30: vu.append((commande, timeout)) or (0, "", "")
    )
    commands.run_action("lire_fstab")
    assert vu == [(["cat", "/etc/fstab"], 10)]


def test_service_isole_dans_compose():
    import yaml

    racine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    services = yaml.safe_load(open(os.path.join(racine, "compose.yaml")))["services"]
    hote = services["jabba-hote"]
    assert hote["command"] == ["python", "-m", "host.host_service"]
    assert hote["network_mode"] == "none" and "ports" not in hote  # aucun réseau
    assert hote["volumes"] == ["/run/jabba:/run/jabba"]  # ne voit ni les données de l'appli, ni /srv
    assert "/run/jabba:/run/jabba" in services["jabba-app"]["volumes"]
    assert "jabba-hote" in services["jabba-app"]["depends_on"]


def test_gestes_sur_les_outils():
    commande, delai = actions.build(
        "outil", {"dossier": "/opt/stacks/nextcloud", "geste": "demarrer", "adresse": "adresse-nextcloud"}
    )
    assert commande[:2] == ["sh", "-c"] and commande[3:] == ["outil", "/opt/stacks/nextcloud", "adresse-nextcloud"]
    assert '"$1"' in commande[2] and "/opt" not in commande[2]  # le dossier n'est jamais dans le texte du script
    assert actions.build("outil", {"dossier": "/opt/dockge", "geste": "redemarrer"})[0][-1] == ""
    for args in (
        {"dossier": "/opt/stacks/x", "geste": "rm"},
        {"dossier": "/etc", "geste": "demarrer"},
        {"dossier": "/opt/stacks/../../etc", "geste": "demarrer"},
        {"dossier": "/opt/stacks/x; reboot", "geste": "demarrer"},
        {"dossier": "/opt/stacks/x", "geste": "demarrer", "adresse": "x; reboot"},
    ):
        with pytest.raises(actions.ActionError):
            actions.build("outil", args)
    for nom in ("", "-rf", "a b", "x;y"):
        with pytest.raises(actions.ActionError):
            actions.build("conteneur_actif", {"nom": nom})
    with pytest.raises(actions.ActionError):
        actions.build("details_image", {"image": "nextcloud:latest"})
