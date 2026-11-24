"""Comptes Nextcloud : quotas, inscriptions, rangs et dossiers communs, avec un faux `occ`."""

import json

import pytest

from services import nextcloud_accounts


class FakeOcc:
    """Imite les commandes occ utilisées par Jabba, sur un petit état en mémoire."""

    def __init__(self):
        self.users = {}  # uid -> dict
        self.groups = {"admin": [], nextcloud_accounts.MEMBERS_GROUP: []}
        self.settings = {}
        self.calls = []

    def add_item(self, uid, enabled=True, pending=False, group_list=()):
        self.users[uid] = {
            "display_name": uid.capitalize(),
            "email": "",
            "enabled": enabled,
            "quota": "5 GB",
            "groups": list(group_list),
            "pending": pending,
            "last_seen": "never",
        }
        for g in group_list:
            self.groups.setdefault(g, []).append(uid)

    def __call__(self, args, input_text=None, is_password=False, timeout=60):
        self.calls.append(args)
        a = [x for x in args if x != "--output=json"]
        c = a[0]
        ok = lambda s="": (0, s, "")
        if c == "user:list":
            res = {u: dict(v, groups=[g for g, m in self.groups.items() if u in m]) for u, v in self.users.items()}
            if "--disabled" in a:
                return ok(json.dumps({u: v["display_name"] for u, v in self.users.items() if not v["enabled"]}))
            return ok(json.dumps(res))
        if c == "user:setting":
            uid, app, key = a[1], a[2], a[3]
            if len(a) > 4:
                if (app, key) == ("files", "quota"):
                    self.users[uid]["quota"] = a[4]
                return ok()
            if (app, key) == ("registration", "send_welcome_mail_on_enable") and self.users[uid]["pending"]:
                return ok("yes\n")
            return 1, "", ""
        if c == "user:enable":
            self.users[a[1]].update(enabled=True, pending=False)
            return ok()
        if c == "user:disable":
            self.users[a[1]]["enabled"] = False
            return ok()
        if c == "user:delete":
            self.users.pop(a[1])
            for m in self.groups.values():
                if a[1] in m:
                    m.remove(a[1])
            return ok()
        if c == "group:list":
            return ok(json.dumps(self.groups))
        if c == "group:add":
            self.groups.setdefault(a[-1], [])
            return ok()
        if c == "group:delete":
            self.groups.pop(a[1], None)
            return ok()
        if c == "group:info":
            return (0, "", "") if a[1] in self.groups else (1, "", "")
        if c in ("group:adduser", "group:removeuser"):
            g, u = a[1], a[2]
            m = self.groups.setdefault(g, [])
            if c == "group:adduser" and u not in m:
                m.append(u)
            if c == "group:removeuser" and u in m:
                m.remove(u)
            return ok()
        if c == "user:add":
            uid = a[-1]
            self.add_item(uid)
            if "--group" in a:
                self.groups[a[a.index("--group") + 1]].append(uid)
            self.password = input_text
            return ok()
        if c == "user:resetpassword":
            self.password = input_text
            return ok()
        if c == "config:app:set":
            self.settings[(a[1], a[2])] = a[a.index("--value") + 1]
            return ok()
        if c == "app:enable":
            return ok()
        if c == "config:app:get":
            v = self.settings.get((a[1], a[2]))
            return (0, v + "\n", "") if v is not None else (1, "", "")
        if c == "config:app:delete":
            self.settings.pop((a[1], a[2]), None)
            return ok()
        return 1, "", "commande inconnue"


@pytest.fixture
def occ(tmp_path, monkeypatch):
    monkeypatch.setattr(nextcloud_accounts, "STATE_FILE", str(tmp_path / "nuage.json"))
    nextcloud_accounts.USAGE_CACHE["v"].clear()
    f = FakeOcc()
    f.add_item("rob", group_list=["admin", nextcloud_accounts.MEMBERS_GROUP])
    f.add_item("alice", group_list=[nextcloud_accounts.MEMBERS_GROUP])
    f.add_item("bob", enabled=False, pending=True)
    f.add_item("carole", enabled=False)
    return f


def test_sizes():
    assert nextcloud_accounts.to_bytes("5 GB") == 5 * 1024**3
    assert nextcloud_accounts.to_bytes("500 MB") == 500 * 1024**2
    assert nextcloud_accounts.to_bytes("none") is None
    assert (
        nextcloud_accounts.quota_text(5) == "5 GB"
        and nextcloud_accounts.quota_text(2.5) == "2560 MB"
        and nextcloud_accounts.quota_text(None) == "none"
    )


def test_account_states(occ):
    c = {x["id"]: x for x in nextcloud_accounts.accounts(occ)}
    assert c["bob"]["state"] == "attente"  # inscrit, en attente de validation
    assert c["carole"]["state"] == "suspendu"  # désactivé par un admin
    assert c["alice"]["state"] == "actif" and c["rob"]["admin"]
    assert [x["id"] for x in nextcloud_accounts.accounts(occ)][0] == "bob"  # les demandes d'abord


def test_approve(occ):
    nextcloud_accounts.approve(occ, "bob")
    assert occ.users["bob"]["enabled"] and "bob" in occ.groups[nextcloud_accounts.MEMBERS_GROUP]


def test_add_password_never_on_the_command_line(occ):
    nextcloud_accounts.add(occ, "denis", "Denis", "", "Mot-de-passe-123456")
    assert occ.password == "Mot-de-passe-123456"
    assert not any("Mot-de-passe-123456" in " ".join(a) for a in occ.calls)
    assert "denis" in occ.groups[nextcloud_accounts.MEMBERS_GROUP]


def test_invalid_login(occ):
    for bad in ("Denis", "a", "d e", "../x", "x" * 40):
        with pytest.raises(nextcloud_accounts.CloudError):
            nextcloud_accounts.add(occ, bad, "", "", "Mot-de-passe-123456")


def test_set_up(occ):
    nextcloud_accounts.set_up(occ)
    assert occ.settings[("registration", "admin_approval_required")] == "yes"
    assert occ.settings[("registration", "registered_user_group")] == nextcloud_accounts.MEMBERS_GROUP
    assert occ.settings[("files", "default_quota")] == "5 GB"


def test_cumulative_ranks(occ):
    d = nextcloud_accounts.read()
    d["ranks_enabled"] = True
    member = nextcloud_accounts.create_rank(occ, d, "Adhérent")
    employee = nextcloud_accounts.create_rank(occ, d, "Employé")
    manager = nextcloud_accounts.create_rank(occ, d, "Gérant")
    d["rank_of"] = {"alice": manager, "rob": member}
    nextcloud_accounts.sync_ranks(occ, d)
    assert set(occ.groups[member]) == {"alice", "rob"}
    assert occ.groups[employee] == ["alice"] and occ.groups[manager] == ["alice"]
    # On descend Employé sous Adhérent : rob (Adhérent) entre dans Employé.
    d["ranks"][0], d["ranks"][1] = d["ranks"][1], d["ranks"][0]
    nextcloud_accounts.sync_ranks(occ, d)
    assert set(occ.groups[employee]) == {"alice", "rob"} and set(occ.groups[member]) == {"alice", "rob"}
    assert occ.groups[manager] == ["alice"]
    # Rangs désactivés : plus personne dans les groupes de rang (les rangs restent notés).
    d["ranks_enabled"] = False
    nextcloud_accounts.sync_ranks(occ, d)
    assert all(not occ.groups[r] for r in (member, employee, manager)) and d["rank_of"]["alice"] == manager


def test_deleting_a_rank_moves_members_down(occ):
    d = nextcloud_accounts.read()
    d["ranks_enabled"] = True
    bottom = nextcloud_accounts.create_rank(occ, d, "Bas")
    top = nextcloud_accounts.create_rank(occ, d, "Haut")
    d["rank_of"] = {"alice": top, "rob": bottom}
    nextcloud_accounts.delete_rank(occ, d, top)
    assert d["rank_of"] == {"alice": bottom, "rob": bottom} and top not in occ.groups
    nextcloud_accounts.delete_rank(occ, d, bottom)
    assert d["rank_of"] == {}


def test_duplicate_rank_refused(occ):
    d = nextcloud_accounts.read()
    nextcloud_accounts.create_rank(occ, d, "Adhérent")
    with pytest.raises(nextcloud_accounts.CloudError):
        nextcloud_accounts.create_rank(occ, d, "adhérent")


def test_protected_groups(occ):
    for g in ("admin", nextcloud_accounts.MEMBERS_GROUP, "rang-abc123"):
        with pytest.raises(nextcloud_accounts.CloudError):
            nextcloud_accounts.delete_group(occ, g)
    with pytest.raises(nextcloud_accounts.CloudError):
        nextcloud_accounts.create_group(occ, "rang-pirate")


def test_folder_modes():
    assert nextcloud_accounts.MODES["lecture"][0] == ["read"]
    assert "delete" not in nextcloud_accounts.MODES["depot"][0] and "share" not in nextcloud_accounts.MODES["depot"][0]
    assert (
        nextcloud_accounts.MODES_BY_PERMISSIONS[1] == "lecture"
        and nextcloud_accounts.MODES_BY_PERMISSIONS[7] == "depot"
        and nextcloud_accounts.MODES_BY_PERMISSIONS[31] == "complet"
    )


def test_charter(occ):
    assert nextcloud_accounts.charter(occ) == ""
    nextcloud_accounts.set_up(occ)
    assert (
        nextcloud_accounts.charter(occ) == nextcloud_accounts.DEFAULT_CHARTER
    )  # le modèle est posé à la mise en route
    nextcloud_accounts.set_charter(occ, "Ligne un.\n\n  Ligne   deux.")
    assert (
        nextcloud_accounts.charter(occ) == "Ligne un. Ligne deux."
    )  # une seule ligne : le formulaire n'affiche pas les retours
    with pytest.raises(nextcloud_accounts.CloudError):
        nextcloud_accounts.set_charter(occ, "x" * (nextcloud_accounts.CHARTER_MAX + 1))
    nextcloud_accounts.set_charter(occ, "")
    assert nextcloud_accounts.charter(occ) == ""
    nextcloud_accounts.set_up(occ)
    assert nextcloud_accounts.charter(occ) == nextcloud_accounts.DEFAULT_CHARTER
    nextcloud_accounts.set_charter(occ, "Ma charte.")
    nextcloud_accounts.set_up(occ)
    assert nextcloud_accounts.charter(occ) == "Ma charte."  # relancer la mise en route ne l'écrase pas


def test_charter_mentions_the_essentials():
    for word in ("administrateur", "Cryptomator", "sauvegardé", "suppression"):
        assert word in nextcloud_accounts.DEFAULT_CHARTER


class FakeOccDisks(FakeOcc):
    """FauxOcc avec des stockages externes (disques branchés au serveur)."""

    def __init__(self, mounts):
        super().__init__()
        self.mounts = mounts

    def __call__(self, args, input_text=None, is_password=False, timeout=60):
        a = [x for x in args if x != "--output=json"]
        if a[0] == "files_external:list":
            self.calls.append(args)
            return 0, json.dumps(self.mounts), ""
        if a[0] == "files_external:applicable":
            self.calls.append(args)
            m = next(m for m in self.mounts if str(m["mount_id"]) == a[-1])
            m["applicable_groups"].append(a[1].split("=", 1)[1])
            return 0, "", ""
        return super().__call__(args, input_text, is_password, timeout)


def _mount(i, nom, users=(), groups=()):
    return {"mount_id": i, "mount_point": "/" + nom, "applicable_users": list(users), "applicable_groups": list(groups)}


def test_disks_reserved_to_admins_before_any_new_account(tmp_path, monkeypatch):
    monkeypatch.setattr(nextcloud_accounts, "STATE_FILE", str(tmp_path / "nuage.json"))
    f = FakeOccDisks([_mount(1, "R2-D2"), _mount(2, "Photos", groups=["famille"]), _mount(3, "Perso", users=["rob"])])
    f.add_item("rob", group_list=["admin"])
    nextcloud_accounts.add(f, "denis", "Denis", "", "Mot-de-passe-123456")
    assert f.mounts[0]["applicable_groups"] == ["admin"]  # était ouvert à tous : réservé aux admins
    assert f.mounts[1]["applicable_groups"] == ["famille"]  # réglé à la main : pas touché
    assert f.mounts[2]["applicable_groups"] == []
    order = [a[0] for a in f.calls]
    assert order.index("files_external:applicable") < order.index("user:add")  # avant que le compte existe


def test_disks_reserved_before_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(nextcloud_accounts, "STATE_FILE", str(tmp_path / "nuage.json"))
    f = FakeOccDisks([_mount(7, "R2-D2")])
    f.add_item("bob", enabled=False, pending=True)
    nextcloud_accounts.approve(f, "bob")
    order = [a[0] for a in f.calls]
    assert f.mounts[0]["applicable_groups"] == ["admin"]
    assert order.index("files_external:applicable") < order.index("user:enable")
    assert nextcloud_accounts.reserve_disks_for_admins(f) == []  # rien à refaire


def test_disks_without_external_storage_app(occ):
    assert nextcloud_accounts.reserve_disks_for_admins(occ) == []  # files_external absent : rien à faire, pas d'erreur
