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

    def faux(commande, delai, entree=None):
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
    monkeypatch.setattr(host_service, "run", lambda commande, delai, entree=None: (0, " ".join(commande), ""))
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
        commands,
        "run_on_host",
        lambda commande, timeout=30, input_text=None: vu.append((commande, timeout)) or (0, "", ""),
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


def test_nextcloud_seulement_les_commandes_prevues():
    commande, delai = actions.build("nextcloud", {"commande": "user:list", "arguments": ["--output=json"]})
    assert commande[:2] == ["docker", "exec"] and commande[-2:] == ["user:list", "--output=json"] and delai == 60
    # Un texte avec « ; » ou des guillemets reste un seul argument : jamais interprété par un shell.
    commande, _ = actions.build(
        "nextcloud", {"commande": "config:app:set", "arguments": ["a", "b", "--value", "x; reboot"]}
    )
    assert commande[-1] == "x; reboot" and "sh" not in commande
    for args in (
        {"commande": "maintenance:mode"},
        {"commande": "db:convert-type"},
        {"commande": "user:list", "arguments": "--output=json"},
        {"commande": "user:list", "arguments": ["a\x00b"]},
        {"commande": "user:list", "delai": 999999},
    ):
        with pytest.raises(actions.ActionError):
            actions.build("nextcloud", args)


def test_mot_de_passe_par_entree_standard():
    commande, _ = actions.build(
        "nextcloud", {"commande": "user:add", "arguments": ["--password-from-env", "alice"], "mot_de_passe": True}
    )
    assert commande[:2] == ["sh", "-c"] and "OC_PASS" in commande
    assert actions.check_input("nextcloud", "secret-tres-long") == "secret-tres-long"
    with pytest.raises(actions.ActionError):
        actions.check_input("liste_conteneurs", "texte")  # aucune autre action ne reçoit de texte
    vu = []

    def runner(commande, delai, entree=None):
        vu.append(entree)
        return 0, "", ""

    demande = json.dumps(
        {
            "action": "nextcloud",
            "args": {
                "commande": "user:resetpassword",
                "arguments": ["--password-from-env", "alice"],
                "mot_de_passe": True,
            },
            "entree": "secret-tres-long",
        }
    )
    assert host_service.handle(demande, runner)["code"] == 0 and vu == ["secret-tres-long"]
    demande = json.dumps({"action": "espace_disques", "args": {"chemins": ["/"]}, "entree": "x"})
    assert host_service.handle(demande, runner)["code"] == host_service.REFUSED


def test_occ_passe_par_la_liste_fermee(monkeypatch):
    vu = []
    monkeypatch.setattr(
        commands,
        "run_on_host",
        lambda commande, timeout=30, input_text=None: vu.append((commande, input_text)) or (0, "ok", ""),
    )
    monkeypatch.setattr(commands.config, "DEMO", False)
    assert commands.occ(["user:list", "--output=json"]) == (0, "ok", "")
    assert (
        commands.occ(["user:add", "--password-from-env", "bob"], input_text="motdepasse-long", is_password=True)[0] == 0
    )
    assert vu[1][1] == "motdepasse-long" and "motdepasse-long" not in vu[1][0]
    assert commands.occ(["maintenance:mode", "--on"])[0] == 1  # refusée, rien lancé
    assert len(vu) == 2


def test_actions_systeme_sans_parametre():
    for nom in (
        "version_systeme",
        "noyau",
        "redemarrage_requis",
        "journal_mises_a_jour",
        "appareils_tailscale",
        "alimentation",
        "redemarrer_serveur",
        "lire_regle_redemarrage",
        "minuteur_redemarrage",
        "dossier_appli",
    ):
        commande, delai = actions.build(nom, {})
        assert commande and 0 < delai <= 60


def test_regle_de_redemarrage():
    commande, _ = actions.build("regle_redemarrage", {"mode": "auto"})
    assert commande[:2] == ["sh", "-c"] and "echo auto >" in commande[2]
    with pytest.raises(actions.ActionError):
        actions.build("regle_redemarrage", {"mode": "auto; reboot"})


def test_mise_a_jour_de_l_appli_cle_lue_par_le_service(monkeypatch):
    from services import app_update

    cle = "ssh-ed25519 " + "A" * 68
    monkeypatch.setattr(app_update, "publication_key", lambda: cle)
    commande, _ = actions.build(
        "installer_appli", {"dossier": "/opt/jabba", "tag": "v0.14.0", "unite": "jabba-maj-1700000000"}
    )
    assert commande[:2] == ["sh", "-c"] and commande[3:6] == ["tache", actions.TASKS_DIR, "jabba-maj-1700000000"]
    script = commande[6]
    assert cle in script and "${LIGNE% *}" in script  # le script arrive intact, ${…} compris
    assert "$3" in commande[2] and "/opt/jabba" not in commande[2]  # jamais collé dans le texte du shell
    for args in (
        {"dossier": "/opt/jabba", "tag": "v0.14.0; reboot", "unite": "jabba-maj-1"},
        {"dossier": "/opt/../etc", "tag": "v0.14.0", "unite": "jabba-maj-1"},
        {"dossier": "/opt/jabba", "tag": "v0.14.0", "unite": "sshd"},
    ):
        with pytest.raises(actions.ActionError):
            actions.build("installer_appli", args)
    monkeypatch.setattr(app_update, "publication_key", lambda: None)  # sans clé chez le service : rien ne s'installe
    with pytest.raises(actions.ActionError):
        actions.build("installer_appli", {"dossier": "/opt/jabba", "tag": "v0.14.0", "unite": "jabba-maj-1"})


def test_tache_de_fond_ecrit_puis_lance_le_script(tmp_path):
    import subprocess

    commande, _ = actions._background("jabba-test-1", 'echo "${HOME:+ok}"')
    texte = commande[2].replace("exec systemd-run", "cat").replace(' --unit="$2" --collect sh', "")
    sortie = subprocess.run(
        ["sh", "-c", texte, "tache", str(tmp_path), "jabba-test-1", commande[6]], capture_output=True, text=True
    )
    assert sortie.stdout == 'rm -f "$0"\necho "${HOME:+ok}"'
    assert (tmp_path / "jabba-test-1.sh").read_text() == sortie.stdout


def test_suivi_des_taches():
    assert actions.build("etat_tache", {"unite": "jabba-maj-12"})[0][-1] == "jabba-maj-12"
    for unite in ("ssh", "jabba-maj-", "jabba-maj-1;x", "--all"):
        with pytest.raises(actions.ActionError):
            actions.build("journal_tache", {"unite": unite})


# ---------------------------------------------------------------- disques : /etc/fstab modifié par le service
FSTAB_DEPART = "# /etc/fstab\nUUID=aaaa / ext4 defaults 0 1\nUUID=bbbb /srv/data ext4 defaults 0 2"  # sans fin de ligne


def _lancer_sur_faux_serveur(tmp_path, commande):
    """Joue le script d'une action sur un faux /etc/fstab ; systemctl, umount, mkdir… ne font rien."""
    import subprocess

    outils = tmp_path / "outils"
    outils.mkdir(exist_ok=True)
    for outil, texte in {
        "systemctl": "exit 0",
        "umount": "exit 0",
        "mkdir": "exit 0",
        "rmdir": "exit 0",
        "systemd-escape": 'echo "srv-disques-x.automount"',
    }.items():
        (outils / outil).write_text("#!/bin/sh\n" + texte + "\n")
        (outils / outil).chmod(0o755)
    fstab = tmp_path / "fstab"
    script = commande[2].replace("F=/etc/fstab", f"F={fstab}", 1)
    env = {**os.environ, "PATH": f"{outils}:{os.environ['PATH']}"}
    return subprocess.run(["sh", "-c", script, *commande[3:]], capture_output=True, text=True, env=env)


def test_disque_declare_regle_puis_oublie(tmp_path):
    fstab = tmp_path / "fstab"
    fstab.write_text(FSTAB_DEPART)
    declarer = actions.build(
        "declarer_disque", {"uuid": "1234-abcd", "format": "exfat", "dossier": "Mes-photos", "nom": "Mes photos"}
    )[0]
    assert _lancer_sur_faux_serveur(tmp_path, declarer).returncode == 0
    lignes = fstab.read_text().splitlines()
    assert lignes[:3] == FSTAB_DEPART.splitlines()  # rien d'autre n'a bougé
    assert lignes[3] == "# Ajouté par l'appli Jabba : Mes photos"
    assert lignes[4].startswith("UUID=1234-ABCD /srv/disques/Mes-photos exfat ro,uid=1000")
    assert (tmp_path / "fstab.jabba-bak").read_text() == FSTAB_DEPART
    assert _lancer_sur_faux_serveur(tmp_path, declarer).returncode == 3  # déjà déclaré : refusé

    ecriture = actions.build("mode_disque", {"chemin": "/srv/disques/Mes-photos", "ecriture": True})[0]
    assert _lancer_sur_faux_serveur(tmp_path, ecriture).returncode == 0
    ligne = fstab.read_text().splitlines()[4]
    assert ligne.split()[3].startswith("rw,uid=1000") and ",ro" not in ligne
    assert fstab.read_text().splitlines()[:4] == lignes[:4]
    lecture = actions.build("mode_disque", {"chemin": "/srv/disques/Mes-photos", "ecriture": "oui"})[
        0
    ]  # seul True vaut écriture
    _lancer_sur_faux_serveur(tmp_path, lecture)
    assert fstab.read_text().splitlines()[4].split()[3].startswith("ro,uid=1000")
    absent = actions.build("mode_disque", {"chemin": "/srv/disques/Autre", "ecriture": True})[0]
    avant = fstab.read_text()
    assert _lancer_sur_faux_serveur(tmp_path, absent).returncode == 4 and fstab.read_text() == avant

    oublier = actions.build("oublier_disque", {"chemin": "/srv/disques/Mes-photos", "nom": "Mes photos"})[0]
    assert _lancer_sur_faux_serveur(tmp_path, oublier).returncode == 0
    assert fstab.read_text() == FSTAB_DEPART + "\n"


def test_disques_parametres_refuses():
    for args in (
        {"uuid": "1234; reboot", "format": "exfat", "dossier": "x", "nom": "x"},
        {"uuid": "1234-abcd", "format": "btrfs", "dossier": "x", "nom": "x"},
        {"uuid": "1234-abcd", "format": "exfat", "dossier": "../etc", "nom": "x"},
        {"uuid": "1234-abcd", "format": "exfat", "dossier": "a b", "nom": "x"},
        {"uuid": "1234-abcd", "format": "exfat", "dossier": "x", "nom": "x\nUUID=1 / ext4"},
    ):
        with pytest.raises(actions.ActionError):
            actions.build("declarer_disque", args)
    for chemin in ("/srv/data", "/etc", "/srv/disques/../data", "/srv/disques/"):
        with pytest.raises(actions.ActionError):
            actions.build("oublier_disque", {"chemin": chemin, "nom": "x"})
    with pytest.raises(actions.ActionError):
        actions.build(
            "mode_disque", {"chemin": "/srv/data", "ecriture": False}
        )  # le disque de données reste en écriture
    with pytest.raises(actions.ActionError):
        actions.build("disque_present", {"uuid": "-o x"})


def test_ajout_automatique_d_un_disque_usb(tmp_path, monkeypatch):
    from services import auto_disks

    monkeypatch.setattr(auto_disks, "DISKS_FILE", str(tmp_path / "disques.json"))
    arbre = {
        "blockdevices": [
            {"type": "disk", "tran": "sata", "children": [{"uuid": "aaaa", "fstype": "ext4", "size": 10**12}]},
            {
                "type": "disk",
                "tran": "usb",
                "children": [
                    {"uuid": "c12a-0001", "fstype": "vfat", "label": "EFI", "size": 10**9},
                    {"uuid": "5e5e-0002", "fstype": "exfat", "label": "Vacances", "size": 10**11, "path": "/dev/sdb2"},
                    {"uuid": "dead-0003", "fstype": "apfs", "label": "Mac", "size": 10**11},
                ],
            },
        ]
    }
    demandes = []

    def faux(action, input_text=None, **args):
        demandes.append((action, args))
        if action == "arbre_disques":
            return 0, json.dumps(arbre), ""
        if action == "lire_fstab":
            return 0, "UUID=aaaa / ext4 defaults 0 1\n", ""
        return 0, "", ""

    monkeypatch.setattr(commands, "run_action", faux)
    alertes = []
    auto_disks.scan(lambda: False, lambda *a: alertes.append(a[1]), lambda: [])
    ajouts = [args for nom, args in demandes if nom == "declarer_disque"]
    assert ajouts == [{"uuid": "5e5e-0002", "format": "exfat", "dossier": "Vacances", "nom": "Vacances"}]
    actions.build("declarer_disque", ajouts[0])  # la demande passe la vérification du service
    assert auto_disks.known_disks() == {"Vacances": "/srv/disques/Vacances"}
    assert alertes == ["Disque illisible", "Nouveau disque : Vacances"]

    demandes.clear()
    assert auto_disks.forget("Vacances")["uuid"] == "5e5e-0002"
    assert demandes == [("oublier_disque", {"chemin": "/srv/disques/Vacances", "nom": "Vacances"})]
    assert auto_disks.known_disks() == {} and "5e5e-0002" in auto_disks.read()["oublies"]
