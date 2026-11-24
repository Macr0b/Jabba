"""Une adresse à part par outil : clé Tailscale vérifiée puis oubliée, machine inscrite sans expiration,
conteneur lancé avec une clé d'inscription à usage unique, jamais avec la clé d'accès."""

import httpx
import pytest

from host import actions
from services import tool_addresses, tools

TOKEN = "tskey-api-kAbCdEf123-XyZ0123456789"
AUTH = "tskey-auth-k1234567-AbCdEfGh"


class FakeTailscale:
    def __init__(self):
        self.calls = []
        self.refused = False
        self.machines = []

    def __call__(self, method, url, json=None, headers=None, timeout=None):
        chemin = url.removeprefix(tool_addresses.API)
        self.calls.append((method, chemin, json, headers["Authorization"]))
        if self.refused:
            return httpx.Response(401, json={"message": "invalid"})
        if method == "GET" and chemin == "/tailnet/-/devices":
            return httpx.Response(200, json={"devices": self.machines})
        if method == "POST" and chemin == "/tailnet/-/keys":
            return httpx.Response(200, json={"key": AUTH})
        return httpx.Response(200, content=b"")


@pytest.fixture
def ts(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_addresses, "TOKEN_FILE", str(tmp_path / "tailscale-api.json"))
    monkeypatch.setattr(tool_addresses, "ADDRESSES_FILE", str(tmp_path / "adresses.json"))
    monkeypatch.setattr(tool_addresses.config, "DEMO", False)
    monkeypatch.setattr(tool_addresses.activity_log, "record", lambda *a, **k: None)
    from services import tool_setup

    addresses_told = []
    monkeypatch.setattr(tool_setup, "after_address", lambda outil, url: addresses_told.append((outil, url)))
    tool_addresses._STATE.clear()
    fake = FakeTailscale()
    monkeypatch.setattr(tool_addresses.httpx, "request", fake)
    # Le fil de création tourne tout de suite, dans le test.
    monkeypatch.setattr(
        tool_addresses.threading, "Thread", lambda target, daemon: type("T", (), {"start": staticmethod(target)})()
    )
    actions_run = []

    def action(nom, input_text=None, **args):
        actions_run.append((nom, input_text, args))
        if nom == "adresse_outil":  # le conteneur s'inscrit : la machine apparaît dans Tailscale
            fake.machines.append(
                {
                    "id": f"n-{args['outil']}",
                    "hostname": args["outil"],
                    "name": f"{args['outil']}.tail1234.ts.net",
                    "created": "2099-01-01T00:00:00Z",
                }
            )
        return 0, "", ""

    monkeypatch.setattr(tool_addresses.commands, "run_action", action)
    fake.actions = actions_run
    return fake


def test_key_checked_then_kept_private(ts, tmp_path):
    tool_addresses.save_token("  " + TOKEN + "\n")
    assert ts.calls == [("GET", "/tailnet/-/devices", None, f"Bearer {TOKEN}")]
    assert (tmp_path / "tailscale-api.json").stat().st_mode & 0o077 == 0
    assert tool_addresses.status(set())["key_ready"]


@pytest.mark.parametrize(
    "key",
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
def test_key_refused_before_any_call(ts, key):
    with pytest.raises(tool_addresses.AddressError):
        tool_addresses.save_token(key)
    assert not ts.calls and not tool_addresses.status(set())["key_ready"]


def test_key_refused_by_tailscale_is_not_kept(ts):
    ts.refused = True
    with pytest.raises(tool_addresses.AddressError, match="refuse la clé"):
        tool_addresses.save_token(TOKEN)
    assert not tool_addresses.status(set())["key_ready"]


def test_addresses_created_then_key_forgotten(ts):
    tool_addresses.save_token(TOKEN)
    runs = tool_addresses.create_missing({"paperless", "homebox", "changedetection", "nextcloud", "jabba-app"}, "rob")
    assert runs == ["homebox", "nextcloud", "paperless"]  # changedetection (sans compte), Jabba : rien
    assert tool_addresses.address("paperless") == "https://paperless.tail1234.ts.net"
    assert tool_addresses.address("homebox") == "https://homebox.tail1234.ts.net"
    # Une clé d'inscription à usage unique par outil ; la clé d'accès ne quitte jamais l'appli.
    keys = [a for a in ts.calls if a[1] == "/tailnet/-/keys"]
    assert len(keys) == 3 and all(a[2]["capabilities"]["devices"]["create"]["reusable"] is False for a in keys)
    assert all(a[2]["expirySeconds"] == 3600 for a in keys)
    start = [a for a in ts.actions if a[0] == "adresse_outil"]
    assert [a[1] for a in start] == [AUTH, AUTH, AUTH] and all(TOKEN not in str(a) for a in ts.actions)
    # La machine ne doit pas expirer au bout de 6 mois.
    assert ("POST", "/device/n-paperless/key", {"keyExpiryDisabled": True}, f"Bearer {TOKEN}") in ts.calls
    # Paperless apprend sa nouvelle adresse ; Homebox n'en a pas besoin.
    settings = [a for a in ts.actions if a[0] == "adresse_dans_outil"]
    assert settings == [("adresse_dans_outil", None, {"outil": "paperless", "address": "paperless.tail1234.ts.net"})]
    assert tool_addresses.status(set())["key_ready"]  # gardée : les prochains outils auront leur adresse seuls
    assert tool_addresses.create_missing({"paperless"}, "rob") == []  # déjà là : rien à refaire


def test_a_failure_keeps_the_key_and_says_why(ts, monkeypatch):
    tool_addresses.save_token(TOKEN)
    monkeypatch.setattr(tool_addresses.commands, "run_action", lambda *a, **k: (1, "", "pull access denied"))
    tool_addresses.create_missing({"homebox"})
    etat = tool_addresses.status({"homebox"})
    assert etat["key_ready"]  # on pourra réessayer
    assert etat["tools"] == [{"id": "homebox", "name": "Homebox", "address": None, "state": "pull access denied"}]


def test_machine_that_never_shows_up(ts):
    tool_addresses.save_token(TOKEN)
    clock = iter(range(0, 10_000, 30))
    with pytest.raises(tool_addresses.AddressError, match="pas apparue"):
        tool_addresses._wait_for_device("homebox", since=0, now=lambda: next(clock), sleep=lambda s: None)


def test_old_machine_with_the_same_name_ignored(ts):
    """Une vieille machine « homebox » (hors ligne) : on prend celle inscrite à l'instant (homebox-1)."""
    tool_addresses.save_token(TOKEN)
    ts.machines += [
        {"id": "vieux", "hostname": "homebox", "name": "homebox.tail1234.ts.net", "created": "2020-01-01T00:00:00Z"},
        {"id": "neuf", "hostname": "homebox", "name": "homebox-1.tail1234.ts.net", "created": "2050-01-01T00:00:10Z"},
    ]
    machine = tool_addresses._wait_for_device("homebox", since=tool_addresses._timestamp("2050-01-01T00:00:00Z"))
    assert machine["id"] == "neuf"


def test_without_key_nothing_runs(ts):
    with pytest.raises(tool_addresses.AddressError, match="clé"):
        tool_addresses.create_missing({"paperless"})


def test_address_action_refuses_the_rest():
    for args in ({"outil": "jabba-app"}, {"outil": "../x"}, {"outil": "changedetection"}, {}):
        with pytest.raises(actions.ActionError):
            actions.build("adresse_outil", args)
    commande, _ = actions.build("adresse_outil", {"outil": "paperless"})
    assert commande[-2:] == ["paperless", "8000"]  # le port vient du catalogue, pas de la demande
    with pytest.raises(actions.ActionError):
        actions.host_input("adresse_outil", {"outil": "paperless"}, TOKEN)  # jamais la clé d'accès
    assert actions.host_input("adresse_outil", {"outil": "paperless"}, AUTH) == AUTH
    for adresse in ("paperless.exemple.fr", "a b.ts.net", "x;rm.ts.net", "", None):
        with pytest.raises(actions.ActionError):
            actions.build("adresse_dans_outil", {"outil": "paperless", "address": adresse})
    with pytest.raises(actions.ActionError):
        actions.build("adresse_dans_outil", {"outil": "homebox", "address": "homebox.tail1234.ts.net"})
    commande, _ = actions.build("adresse_dans_outil", {"outil": "paperless", "address": "paperless.tail1234.ts.net"})
    assert commande[-3:] == ["paperless", "PAPERLESS_URL", "paperless.tail1234.ts.net"]


def test_tool_link_takes_its_address(ts, monkeypatch):
    monkeypatch.setattr(tools.config, "DEMO", True)
    before = {t["id"]: t["link"] for t in tools.list_tools()}
    tool_addresses.json_files.save(
        tool_addresses.ADDRESSES_FILE, {"adguard-home": "https://adguard-home.tail1234.ts.net"}
    )
    after = {t["id"]: t["link"] for t in tools.list_tools()}
    assert after["adguard-home"] == "https://adguard-home.tail1234.ts.net" != before["adguard-home"]
    assert after["jellyfin"] == before["jellyfin"]


def test_address_created_during_activation(ts):
    """Un outil qu'on allume reçoit son adresse dans la foulée, sans rien demander (clé gardée)."""
    tool_addresses.save_token(TOKEN)
    assert tool_addresses.after_install("homebox", "rob") == "Adresse : https://homebox.tail1234.ts.net"
    assert tool_addresses.after_install("homebox", "rob") == ""  # déjà là
    assert tool_addresses.after_install("changedetection", "rob") == ""  # pas de compte : pas d'adresse
    assert tool_addresses.after_install("time-machine", "rob") == ""  # pas de page


def test_activation_without_key_nothing_fails(ts):
    assert tool_addresses.after_install("homebox", "rob") == ""
    assert not ts.calls and tool_addresses.address("homebox") is None  # la carte de réparation le dira


def test_expired_key_forgotten_and_reported(ts):
    tool_addresses.save_token(TOKEN)
    ts.refused = True
    message = tool_addresses.after_install("homebox", "rob")
    assert message.startswith("Adresse à part non créée") and "expirée" in message
    assert not tool_addresses.status(set())["key_ready"]  # inutile de garder une clé morte
    assert tool_addresses.status({"homebox"})["tools"][0]["state"].startswith("Tailscale refuse la clé")


def test_install_then_address_in_the_same_task(ts, monkeypatch):
    from services import background_tasks, optional_tools

    tool_addresses.save_token(TOKEN)
    monkeypatch.setattr(background_tasks.config, "DEMO", False)
    monkeypatch.setattr(optional_tools.actions, "optional_password", lambda *a: None)
    monkeypatch.setattr(
        background_tasks.threading, "Thread", lambda target, daemon: type("T", (), {"start": staticmethod(target)})()
    )
    monkeypatch.setattr(background_tasks.activity_log, "record", lambda *a, **k: None)
    started_ones = []

    def action(nom, input_text=None, **args):
        started_ones.append(nom)
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
    task = optional_tools.install("homebox", "rob")
    assert started_ones == ["installer_outil_option", "adresse_outil"]
    assert task["state"] == "terminé" and "Adresse : https://homebox.tail1234.ts.net" in task["log"]


def test_address_already_there_from_an_old_install(ts, monkeypatch):
    """Un conteneur « adresse-dockge » d'une ancienne pile : gardé, et sa machine (ancienne) reprise."""
    tool_addresses.save_token(TOKEN)
    ts.machines.append(
        {"id": "n-ancien", "hostname": "dockge", "name": "dockge.tail1234.ts.net", "created": "2026-01-01T00:00:00Z"}
    )
    monkeypatch.setattr(tool_addresses.commands, "run_action", lambda *a, **k: (0, "DEJA\n", ""))
    assert tool_addresses.after_install("dockge") == "Adresse : https://dockge.tail1234.ts.net"
    assert ("POST", "/device/n-ancien/key", {"keyExpiryDisabled": True}, f"Bearer {TOKEN}") in ts.calls


def test_script_keeps_the_container_of_another_stack(tmp_path):
    """Essai à blanc du script : docker répond qu'adresse-dockge appartient à la pile « adresses »."""
    import os
    import subprocess

    fake = tmp_path / "docker"
    fake.write_text(
        '#!/bin/sh\n[ "$1" = inspect ] && { echo adresses; exit 0; }\necho "$@" >> "$(dirname "$0")/appels"\n'
    )
    fake.chmod(0o755)
    commande, _ = actions.build("adresse_outil", {"outil": "dockge"})
    output = subprocess.run(
        commande,
        input=AUTH,
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
    )
    assert output.returncode == 0 and output.stdout.strip() == "DEJA"
    assert (tmp_path / "appels").read_text().strip() == "start adresse-dockge"  # rien d'écrit, rien de recréé
