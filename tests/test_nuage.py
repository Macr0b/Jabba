"""Comptes Nextcloud : quotas, inscriptions, rangs et dossiers communs, avec un faux `occ`."""
import json

import pytest

from services import nextcloud_accounts


class FauxOcc:
    """Imite les commandes occ utilisées par Jabba, sur un petit état en mémoire."""

    def __init__(self):
        self.users = {}       # uid -> dict
        self.groups = {"admin": [], nextcloud_accounts.MEMBERS_GROUP: []}
        self.reglages = {}
        self.appels = []

    def ajouter(self, uid, enabled=True, attente=False, groupes=()):
        self.users[uid] = {"display_name": uid.capitalize(), "email": "", "enabled": enabled, "quota": "5 GB",
                           "groups": list(groupes), "attente": attente, "last_seen": "never"}
        for g in groupes:
            self.groups.setdefault(g, []).append(uid)

    def __call__(self, args, entree=None, mdp=False, timeout=60):
        self.appels.append(args)
        a = [x for x in args if x != "--output=json"]
        c = a[0]
        ok = lambda s="": (0, s, "")
        if c == "user:list":
            res = {u: dict(v, groups=[g for g, m in self.groups.items() if u in m]) for u, v in self.users.items()}
            if "--disabled" in a:
                return ok(json.dumps({u: v["display_name"] for u, v in self.users.items() if not v["enabled"]}))
            return ok(json.dumps(res))
        if c == "user:setting":
            uid, app, cle = a[1], a[2], a[3]
            if len(a) > 4:
                if (app, cle) == ("files", "quota"):
                    self.users[uid]["quota"] = a[4]
                return ok()
            if (app, cle) == ("registration", "send_welcome_mail_on_enable") and self.users[uid]["attente"]:
                return ok("yes\n")
            return 1, "", ""
        if c == "user:enable":
            self.users[a[1]].update(enabled=True, attente=False)
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
            self.ajouter(uid)
            if "--group" in a:
                self.groups[a[a.index("--group") + 1]].append(uid)
            self.mdp = entree
            return ok()
        if c == "user:resetpassword":
            self.mdp = entree
            return ok()
        if c == "config:app:set":
            self.reglages[(a[1], a[2])] = a[a.index("--value") + 1]
            return ok()
        if c == "app:enable":
            return ok()
        if c == "config:app:get":
            v = self.reglages.get((a[1], a[2]))
            return (0, v + "\n", "") if v is not None else (1, "", "")
        if c == "config:app:delete":
            self.reglages.pop((a[1], a[2]), None)
            return ok()
        return 1, "", "commande inconnue"


@pytest.fixture
def occ(tmp_path, monkeypatch):
    monkeypatch.setattr(nextcloud_accounts, "STATE_FILE", str(tmp_path / "nuage.json"))
    nextcloud_accounts.USAGE_CACHE["v"].clear()
    f = FauxOcc()
    f.ajouter("rob", groupes=["admin", nextcloud_accounts.MEMBERS_GROUP])
    f.ajouter("alice", groupes=[nextcloud_accounts.MEMBERS_GROUP])
    f.ajouter("bob", enabled=False, attente=True)
    f.ajouter("carole", enabled=False)
    return f


def test_tailles():
    assert nextcloud_accounts.to_bytes("5 GB") == 5 * 1024 ** 3
    assert nextcloud_accounts.to_bytes("500 MB") == 500 * 1024 ** 2
    assert nextcloud_accounts.to_bytes("none") is None
    assert nextcloud_accounts.quota_text(5) == "5 GB" and nextcloud_accounts.quota_text(2.5) == "2560 MB" and nextcloud_accounts.quota_text(None) == "none"


def test_etats_des_comptes(occ):
    c = {x["id"]: x for x in nextcloud_accounts.accounts(occ)}
    assert c["bob"]["etat"] == "attente"          # inscrit, en attente de validation
    assert c["carole"]["etat"] == "suspendu"      # désactivé par un admin
    assert c["alice"]["etat"] == "actif" and c["rob"]["admin"]
    assert [x["id"] for x in nextcloud_accounts.accounts(occ)][0] == "bob"   # les demandes d'abord


def test_valider(occ):
    nextcloud_accounts.approve(occ, "bob")
    assert occ.users["bob"]["enabled"] and "bob" in occ.groups[nextcloud_accounts.MEMBERS_GROUP]


def test_ajouter_mot_de_passe_jamais_en_ligne_de_commande(occ):
    nextcloud_accounts.add(occ, "denis", "Denis", "", "Mot-de-passe-123456")
    assert occ.mdp == "Mot-de-passe-123456"
    assert not any("Mot-de-passe-123456" in " ".join(a) for a in occ.appels)
    assert "denis" in occ.groups[nextcloud_accounts.MEMBERS_GROUP]


def test_identifiant_invalide(occ):
    for mauvais in ("Denis", "a", "d e", "../x", "x" * 40):
        with pytest.raises(nextcloud_accounts.CloudError):
            nextcloud_accounts.add(occ, mauvais, "", "", "Mot-de-passe-123456")


def test_mise_en_route(occ):
    nextcloud_accounts.set_up(occ)
    assert occ.reglages[("registration", "admin_approval_required")] == "yes"
    assert occ.reglages[("registration", "registered_user_group")] == nextcloud_accounts.MEMBERS_GROUP
    assert occ.reglages[("files", "default_quota")] == "5 GB"


def test_rangs_cumulatifs(occ):
    d = nextcloud_accounts.read()
    d["rangs_actif"] = True
    adherent = nextcloud_accounts.create_rank(occ, d, "Adhérent")
    employe = nextcloud_accounts.create_rank(occ, d, "Employé")
    gerant = nextcloud_accounts.create_rank(occ, d, "Gérant")
    d["rang_de"] = {"alice": gerant, "rob": adherent}
    nextcloud_accounts.sync_ranks(occ, d)
    assert set(occ.groups[adherent]) == {"alice", "rob"}
    assert occ.groups[employe] == ["alice"] and occ.groups[gerant] == ["alice"]
    # On descend Employé sous Adhérent : rob (Adhérent) entre dans Employé.
    d["rangs"][0], d["rangs"][1] = d["rangs"][1], d["rangs"][0]
    nextcloud_accounts.sync_ranks(occ, d)
    assert set(occ.groups[employe]) == {"alice", "rob"} and set(occ.groups[adherent]) == {"alice", "rob"}
    assert occ.groups[gerant] == ["alice"]
    # Rangs désactivés : plus personne dans les groupes de rang (les rangs restent notés).
    d["rangs_actif"] = False
    nextcloud_accounts.sync_ranks(occ, d)
    assert all(not occ.groups[r] for r in (adherent, employe, gerant)) and d["rang_de"]["alice"] == gerant


def test_supprimer_un_rang_fait_descendre(occ):
    d = nextcloud_accounts.read()
    d["rangs_actif"] = True
    bas = nextcloud_accounts.create_rank(occ, d, "Bas")
    haut = nextcloud_accounts.create_rank(occ, d, "Haut")
    d["rang_de"] = {"alice": haut, "rob": bas}
    nextcloud_accounts.delete_rank(occ, d, haut)
    assert d["rang_de"] == {"alice": bas, "rob": bas} and haut not in occ.groups
    nextcloud_accounts.delete_rank(occ, d, bas)
    assert d["rang_de"] == {}


def test_rang_en_double_refuse(occ):
    d = nextcloud_accounts.read()
    nextcloud_accounts.create_rank(occ, d, "Adhérent")
    with pytest.raises(nextcloud_accounts.CloudError):
        nextcloud_accounts.create_rank(occ, d, "adhérent")


def test_groupes_proteges(occ):
    for g in ("admin", nextcloud_accounts.MEMBERS_GROUP, "rang-abc123"):
        with pytest.raises(nextcloud_accounts.CloudError):
            nextcloud_accounts.delete_group(occ, g)
    with pytest.raises(nextcloud_accounts.CloudError):
        nextcloud_accounts.create_group(occ, "rang-pirate")


def test_modes_des_dossiers():
    assert nextcloud_accounts.MODES["lecture"][0] == ["read"]
    assert "delete" not in nextcloud_accounts.MODES["depot"][0] and "share" not in nextcloud_accounts.MODES["depot"][0]
    assert nextcloud_accounts.MODES_BY_PERMISSIONS[1] == "lecture" and nextcloud_accounts.MODES_BY_PERMISSIONS[7] == "depot" and nextcloud_accounts.MODES_BY_PERMISSIONS[31] == "complet"


def test_charte(occ):
    assert nextcloud_accounts.charter(occ) == ""
    nextcloud_accounts.set_up(occ)
    assert nextcloud_accounts.charter(occ) == nextcloud_accounts.DEFAULT_CHARTER          # le modèle est posé à la mise en route
    nextcloud_accounts.set_charter(occ, "Ligne un.\n\n  Ligne   deux.")
    assert nextcloud_accounts.charter(occ) == "Ligne un. Ligne deux."      # une seule ligne : le formulaire n'affiche pas les retours
    with pytest.raises(nextcloud_accounts.CloudError):
        nextcloud_accounts.set_charter(occ, "x" * (nextcloud_accounts.CHARTER_MAX + 1))
    nextcloud_accounts.set_charter(occ, "")
    assert nextcloud_accounts.charter(occ) == ""
    nextcloud_accounts.set_up(occ)
    assert nextcloud_accounts.charter(occ) == nextcloud_accounts.DEFAULT_CHARTER
    nextcloud_accounts.set_charter(occ, "Ma charte.")
    nextcloud_accounts.set_up(occ)
    assert nextcloud_accounts.charter(occ) == "Ma charte."                 # relancer la mise en route ne l'écrase pas


def test_charte_mentionne_l_essentiel():
    for mot in ("administrateur", "Cryptomator", "sauvegardé", "suppression"):
        assert mot in nextcloud_accounts.DEFAULT_CHARTER


class FauxOccDisques(FauxOcc):
    """FauxOcc avec des stockages externes (disques branchés au serveur)."""

    def __init__(self, montages):
        super().__init__()
        self.montages = montages

    def __call__(self, args, entree=None, mdp=False, timeout=60):
        a = [x for x in args if x != "--output=json"]
        if a[0] == "files_external:list":
            self.appels.append(args)
            return 0, json.dumps(self.montages), ""
        if a[0] == "files_external:applicable":
            self.appels.append(args)
            m = next(m for m in self.montages if str(m["mount_id"]) == a[-1])
            m["applicable_groups"].append(a[1].split("=", 1)[1])
            return 0, "", ""
        return super().__call__(args, entree, mdp, timeout)


def _montage(i, nom, users=(), groups=()):
    return {"mount_id": i, "mount_point": "/" + nom, "applicable_users": list(users), "applicable_groups": list(groups)}


def test_disques_reserves_aux_admins_avant_tout_nouveau_compte(tmp_path, monkeypatch):
    monkeypatch.setattr(nextcloud_accounts, "STATE_FILE", str(tmp_path / "nuage.json"))
    f = FauxOccDisques([_montage(1, "R2-D2"), _montage(2, "Photos", groups=["famille"]), _montage(3, "Perso", users=["rob"])])
    f.ajouter("rob", groupes=["admin"])
    nextcloud_accounts.add(f, "denis", "Denis", "", "Mot-de-passe-123456")
    assert f.montages[0]["applicable_groups"] == ["admin"]          # était ouvert à tous : réservé aux admins
    assert f.montages[1]["applicable_groups"] == ["famille"]        # réglé à la main : pas touché
    assert f.montages[2]["applicable_groups"] == []
    ordre = [a[0] for a in f.appels]
    assert ordre.index("files_external:applicable") < ordre.index("user:add")   # avant que le compte existe


def test_disques_reserves_avant_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(nextcloud_accounts, "STATE_FILE", str(tmp_path / "nuage.json"))
    f = FauxOccDisques([_montage(7, "R2-D2")])
    f.ajouter("bob", enabled=False, attente=True)
    nextcloud_accounts.approve(f, "bob")
    ordre = [a[0] for a in f.appels]
    assert f.montages[0]["applicable_groups"] == ["admin"]
    assert ordre.index("files_external:applicable") < ordre.index("user:enable")
    assert nextcloud_accounts.reserve_disks_for_admins(f) == []                          # rien à refaire


def test_disques_sans_appli_stockages_externes(occ):
    assert nextcloud_accounts.reserve_disks_for_admins(occ) == []                        # files_external absent : rien à faire, pas d'erreur
