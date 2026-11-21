"""Une adresse à part par outil : clé Tailscale vérifiée puis oubliée, machine inscrite sans expiration,
conteneur lancé avec une clé d'inscription à usage unique, jamais avec la clé d'accès."""

import httpx
import pytest

from host import actions
from services import tool_addresses, tools

TOKEN = "tskey-api-kAbCdEf123-XyZ0123456789"
AUTH = "tskey-auth-k1234567-AbCdEfGh"


class FauxTailscale:
    def __init__(self):
        self.appels = []
        self.refuse = False
        self.machines = []

    def __call__(self, methode, url, json=None, headers=None, timeout=None):
        chemin = url.removeprefix(tool_addresses.API)
        self.appels.append((methode, chemin, json, headers["Authorization"]))
        if self.refuse:
            return httpx.Response(401, json={"message": "invalid"})
        if methode == "GET" and chemin == "/tailnet/-/devices":
            return httpx.Response(200, json={"devices": self.machines})
        if methode == "POST" and chemin == "/tailnet/-/keys":
            return httpx.Response(200, json={"key": AUTH})
        return httpx.Response(200, content=b"")


@pytest.fixture
def ts(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_addresses, "TOKEN_FILE", str(tmp_path / "tailscale-api.json"))
    monkeypatch.setattr(tool_addresses, "ADDRESSES_FILE", str(tmp_path / "adresses.json"))
    monkeypatch.setattr(tool_addresses.config, "DEMO", False)
    monkeypatch.setattr(tool_addresses.activity_log, "record", lambda *a, **k: None)
    tool_addresses._STATE.clear()
    faux = FauxTailscale()
    monkeypatch.setattr(tool_addresses.httpx, "request", faux)
    # Le fil de création tourne tout de suite, dans le test.
    monkeypatch.setattr(
        tool_addresses.threading, "Thread", lambda target, daemon: type("T", (), {"start": staticmethod(target)})()
    )
    actions_lancees = []

    def action(nom, input_text=None, **args):
        actions_lancees.append((nom, input_text, args))
        if nom == "adresse_outil":  # le conteneur s'inscrit : la machine apparaît dans Tailscale
            faux.machines.append(
                {
                    "id": f"n-{args['outil']}",
                    "hostname": args["outil"],
                    "name": f"{args['outil']}.tail1234.ts.net",
                    "created": "2099-01-01T00:00:00Z",
                }
            )
        return 0, "", ""

    monkeypatch.setattr(tool_addresses.commands, "run_action", action)
    faux.actions = actions_lancees
    return faux


def test_cle_verifiee_puis_gardee_en_prive(ts, tmp_path):
    tool_addresses.save_token("  " + TOKEN + "\n")
    assert ts.appels == [("GET", "/tailnet/-/devices", None, f"Bearer {TOKEN}")]
    assert (tmp_path / "tailscale-api.json").stat().st_mode & 0o077 == 0
    assert tool_addresses.status(set())["cle_prete"]


@pytest.mark.parametrize(
    "cle",
    [
        "",
        None,
        "tskey-auth-k1234567-AbCdEfGh",
        "abc",
        "tskey-api-court",
        "tskey-api-" + "a" * 300,
        f"{TOKEN} ; rm -rf /",
    ],
)
def test_cle_refusee_avant_tout_appel(ts, cle):
    with pytest.raises(tool_addresses.AddressError):
        tool_addresses.save_token(cle)
    assert not ts.appels and not tool_addresses.status(set())["cle_prete"]


def test_cle_que_tailscale_refuse_n_est_pas_gardee(ts):
    ts.refuse = True
    with pytest.raises(tool_addresses.AddressError, match="refuse la clé"):
        tool_addresses.save_token(TOKEN)
    assert not tool_addresses.status(set())["cle_prete"]


def test_creation_des_adresses_puis_cle_oubliee(ts):
    tool_addresses.save_token(TOKEN)
    lances = tool_addresses.create_missing({"paperless", "homebox", "changedetection", "nextcloud", "jabba-app"}, "rob")
    assert lances == ["homebox", "paperless"]  # changedetection (sans compte), Nextcloud (a déjà la sienne) : rien
    assert tool_addresses.address("paperless") == "https://paperless.tail1234.ts.net"
    assert tool_addresses.address("homebox") == "https://homebox.tail1234.ts.net"
    # Une clé d'inscription à usage unique par outil ; la clé d'accès ne quitte jamais l'appli.
    cles = [a for a in ts.appels if a[1] == "/tailnet/-/keys"]
    assert len(cles) == 2 and all(a[2]["capabilities"]["devices"]["create"]["reusable"] is False for a in cles)
    assert all(a[2]["expirySeconds"] == 3600 for a in cles)
    lancement = [a for a in ts.actions if a[0] == "adresse_outil"]
    assert [a[1] for a in lancement] == [AUTH, AUTH] and all(TOKEN not in str(a) for a in ts.actions)
    # La machine ne doit pas expirer au bout de 6 mois.
    assert ("POST", "/device/n-paperless/key", {"keyExpiryDisabled": True}, f"Bearer {TOKEN}") in ts.appels
    # Paperless apprend sa nouvelle adresse ; Homebox n'en a pas besoin.
    reglages = [a for a in ts.actions if a[0] == "adresse_dans_outil"]
    assert reglages == [("adresse_dans_outil", None, {"outil": "paperless", "adresse": "paperless.tail1234.ts.net"})]
    assert tool_addresses.status(set())["cle_prete"]  # gardée : les prochains outils auront leur adresse seuls
    assert tool_addresses.create_missing({"paperless"}, "rob") == []  # déjà là : rien à refaire


def test_un_echec_garde_la_cle_et_dit_pourquoi(ts, monkeypatch):
    tool_addresses.save_token(TOKEN)
    monkeypatch.setattr(tool_addresses.commands, "run_action", lambda *a, **k: (1, "", "pull access denied"))
    tool_addresses.create_missing({"homebox"})
    etat = tool_addresses.status({"homebox"})
    assert etat["cle_prete"]  # on pourra réessayer
    assert etat["outils"] == [{"id": "homebox", "nom": "Homebox", "adresse": None, "etat": "pull access denied"}]


def test_machine_qui_n_apparait_pas(ts):
    tool_addresses.save_token(TOKEN)
    horloge = iter(range(0, 10_000, 30))
    with pytest.raises(tool_addresses.AddressError, match="pas apparue"):
        tool_addresses._wait_for_device("homebox", since=0, now=lambda: next(horloge), sleep=lambda s: None)


def test_ancienne_machine_du_meme_nom_ignoree(ts):
    """Une vieille machine « homebox » (hors ligne) : on prend celle inscrite à l'instant (homebox-1)."""
    tool_addresses.save_token(TOKEN)
    ts.machines += [
        {"id": "vieux", "hostname": "homebox", "name": "homebox.tail1234.ts.net", "created": "2020-01-01T00:00:00Z"},
        {"id": "neuf", "hostname": "homebox", "name": "homebox-1.tail1234.ts.net", "created": "2050-01-01T00:00:10Z"},
    ]
    machine = tool_addresses._wait_for_device("homebox", since=tool_addresses._timestamp("2050-01-01T00:00:00Z"))
    assert machine["id"] == "neuf"


def test_sans_cle_on_ne_lance_rien(ts):
    with pytest.raises(tool_addresses.AddressError, match="clé"):
        tool_addresses.create_missing({"paperless"})


def test_action_d_adresse_refuse_le_reste():
    for args in ({"outil": "nextcloud"}, {"outil": "../x"}, {"outil": "changedetection"}, {}):
        with pytest.raises(actions.ActionError):
            actions.build("adresse_outil", args)
    commande, _ = actions.build("adresse_outil", {"outil": "paperless"})
    assert commande[-2:] == ["paperless", "8000"]  # le port vient du catalogue, pas de la demande
    with pytest.raises(actions.ActionError):
        actions.host_input("adresse_outil", {"outil": "paperless"}, TOKEN)  # jamais la clé d'accès
    assert actions.host_input("adresse_outil", {"outil": "paperless"}, AUTH) == AUTH
    for adresse in ("paperless.exemple.fr", "a b.ts.net", "x;rm.ts.net", "", None):
        with pytest.raises(actions.ActionError):
            actions.build("adresse_dans_outil", {"outil": "paperless", "adresse": adresse})
    with pytest.raises(actions.ActionError):
        actions.build("adresse_dans_outil", {"outil": "homebox", "adresse": "homebox.tail1234.ts.net"})
    commande, _ = actions.build("adresse_dans_outil", {"outil": "paperless", "adresse": "paperless.tail1234.ts.net"})
    assert commande[-3:] == ["paperless", "PAPERLESS_URL", "paperless.tail1234.ts.net"]


def test_le_lien_de_l_outil_prend_son_adresse(ts, monkeypatch):
    monkeypatch.setattr(tools.config, "DEMO", True)
    avant = {t["id"]: t["lien"] for t in tools.list_tools()}
    tool_addresses.json_files.save(
        tool_addresses.ADDRESSES_FILE, {"adguard-home": "https://adguard-home.tail1234.ts.net"}
    )
    apres = {t["id"]: t["lien"] for t in tools.list_tools()}
    assert apres["adguard-home"] == "https://adguard-home.tail1234.ts.net" != avant["adguard-home"]
    assert apres["jellyfin"] == avant["jellyfin"]


def test_adresse_creee_pendant_l_activation(ts):
    """Un outil qu'on allume reçoit son adresse dans la foulée, sans rien demander (clé gardée)."""
    tool_addresses.save_token(TOKEN)
    assert tool_addresses.after_install("homebox", "rob") == "Adresse : https://homebox.tail1234.ts.net"
    assert tool_addresses.after_install("homebox", "rob") == ""  # déjà là
    assert tool_addresses.after_install("changedetection", "rob") == ""  # pas de compte : pas d'adresse
    assert tool_addresses.after_install("time-machine", "rob") == ""  # pas de page


def test_activation_sans_cle_rien_n_echoue(ts):
    assert tool_addresses.after_install("homebox", "rob") == ""
    assert not ts.appels and tool_addresses.address("homebox") is None  # la carte de réparation le dira


def test_cle_expiree_oubliee_et_dite(ts):
    tool_addresses.save_token(TOKEN)
    ts.refuse = True
    message = tool_addresses.after_install("homebox", "rob")
    assert message.startswith("Adresse à part non créée") and "expirée" in message
    assert not tool_addresses.status(set())["cle_prete"]  # inutile de garder une clé morte
    assert tool_addresses.status({"homebox"})["outils"][0]["etat"].startswith("Tailscale refuse la clé")


def test_installation_puis_adresse_dans_la_meme_tache(ts, monkeypatch):
    from services import background_tasks, optional_tools

    tool_addresses.save_token(TOKEN)
    monkeypatch.setattr(background_tasks.config, "DEMO", False)
    monkeypatch.setattr(optional_tools.actions, "optional_password", lambda *a: None)
    monkeypatch.setattr(
        background_tasks.threading, "Thread", lambda target, daemon: type("T", (), {"start": staticmethod(target)})()
    )
    monkeypatch.setattr(background_tasks.activity_log, "record", lambda *a, **k: None)
    lancees = []

    def action(nom, input_text=None, **args):
        lancees.append(nom)
        if nom == "adresse_outil":
            ts.machines.append(
                {
                    "id": "n-h",
                    "hostname": "homebox",
                    "name": "homebox.tail1234.ts.net",
                    "created": "2099-01-01T00:00:00Z",
                }
            )
        return 0, "installé\n", ""

    monkeypatch.setattr(background_tasks.commands, "run_action", action)
    monkeypatch.setattr(tool_addresses.commands, "run_action", action)
    tache = optional_tools.install("homebox", "rob")
    assert lancees == ["installer_outil_option", "adresse_outil"]
    assert tache["etat"] == "terminé" and "Adresse : https://homebox.tail1234.ts.net" in tache["journal"]


def test_adresse_deja_la_d_une_ancienne_installation(ts, monkeypatch):
    """Un conteneur « adresse-dockge » d'une ancienne pile : gardé, et sa machine (ancienne) reprise."""
    tool_addresses.save_token(TOKEN)
    ts.machines.append(
        {"id": "n-ancien", "hostname": "dockge", "name": "dockge.tail1234.ts.net", "created": "2026-01-01T00:00:00Z"}
    )
    monkeypatch.setattr(tool_addresses.commands, "run_action", lambda *a, **k: (0, "DEJA\n", ""))
    assert tool_addresses.after_install("dockge") == "Adresse : https://dockge.tail1234.ts.net"
    assert ("POST", "/device/n-ancien/key", {"keyExpiryDisabled": True}, f"Bearer {TOKEN}") in ts.appels


def test_script_garde_le_conteneur_d_une_autre_pile(tmp_path):
    """Essai à blanc du script : docker répond qu'adresse-dockge appartient à la pile « adresses »."""
    import os
    import subprocess

    faux = tmp_path / "docker"
    faux.write_text(
        '#!/bin/sh\n[ "$1" = inspect ] && { echo adresses; exit 0; }\necho "$@" >> "$(dirname "$0")/appels"\n'
    )
    faux.chmod(0o755)
    commande, _ = actions.build("adresse_outil", {"outil": "dockge"})
    sortie = subprocess.run(
        commande,
        input=AUTH,
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
    )
    assert sortie.returncode == 0 and sortie.stdout.strip() == "DEJA"
    assert (tmp_path / "appels").read_text().strip() == "start adresse-dockge"  # rien d'écrit, rien de recréé
