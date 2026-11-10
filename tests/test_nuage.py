"""Comptes Nextcloud : quotas, inscriptions, rangs et dossiers communs, avec un faux `occ`."""
import json

import pytest

import nuage


class FauxOcc:
    """Imite les commandes occ utilisées par Jabba, sur un petit état en mémoire."""

    def __init__(self):
        self.users = {}       # uid -> dict
        self.groups = {"admin": [], nuage.GROUPE_TOUS: []}
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
        return 1, "", "commande inconnue"


@pytest.fixture
def occ(tmp_path, monkeypatch):
    monkeypatch.setattr(nuage, "FICHIER", str(tmp_path / "nuage.json"))
    nuage.USAGE["v"].clear()
    f = FauxOcc()
    f.ajouter("rob", groupes=["admin", nuage.GROUPE_TOUS])
    f.ajouter("alice", groupes=[nuage.GROUPE_TOUS])
    f.ajouter("bob", enabled=False, attente=True)
    f.ajouter("carole", enabled=False)
    return f


def test_tailles():
    assert nuage.en_octets("5 GB") == 5 * 1024 ** 3
    assert nuage.en_octets("500 MB") == 500 * 1024 ** 2
    assert nuage.en_octets("none") is None
    assert nuage.texte_quota(5) == "5 GB" and nuage.texte_quota(2.5) == "2560 MB" and nuage.texte_quota(None) == "none"


def test_etats_des_comptes(occ):
    c = {x["id"]: x for x in nuage.comptes(occ)}
    assert c["bob"]["etat"] == "attente"          # inscrit, en attente de validation
    assert c["carole"]["etat"] == "suspendu"      # désactivé par un admin
    assert c["alice"]["etat"] == "actif" and c["rob"]["admin"]
    assert [x["id"] for x in nuage.comptes(occ)][0] == "bob"   # les demandes d'abord


def test_valider(occ):
    nuage.valider(occ, "bob")
    assert occ.users["bob"]["enabled"] and "bob" in occ.groups[nuage.GROUPE_TOUS]


def test_ajouter_mot_de_passe_jamais_en_ligne_de_commande(occ):
    nuage.ajouter(occ, "denis", "Denis", "", "Mot-de-passe-123456")
    assert occ.mdp == "Mot-de-passe-123456"
    assert not any("Mot-de-passe-123456" in " ".join(a) for a in occ.appels)
    assert "denis" in occ.groups[nuage.GROUPE_TOUS]


def test_identifiant_invalide(occ):
    for mauvais in ("Denis", "a", "d e", "../x", "x" * 40):
        with pytest.raises(nuage.Erreur):
            nuage.ajouter(occ, mauvais, "", "", "Mot-de-passe-123456")


def test_mise_en_route(occ):
    nuage.mettre_en_route(occ)
    assert occ.reglages[("registration", "admin_approval_required")] == "yes"
    assert occ.reglages[("registration", "registered_user_group")] == nuage.GROUPE_TOUS
    assert occ.reglages[("files", "default_quota")] == "5 GB"


def test_rangs_cumulatifs(occ):
    d = nuage.lire()
    d["rangs_actif"] = True
    adherent = nuage.creer_rang(occ, d, "Adhérent")
    employe = nuage.creer_rang(occ, d, "Employé")
    gerant = nuage.creer_rang(occ, d, "Gérant")
    d["rang_de"] = {"alice": gerant, "rob": adherent}
    nuage.synchroniser(occ, d)
    assert set(occ.groups[adherent]) == {"alice", "rob"}
    assert occ.groups[employe] == ["alice"] and occ.groups[gerant] == ["alice"]
    # On descend Employé sous Adhérent : rob (Adhérent) entre dans Employé.
    d["rangs"][0], d["rangs"][1] = d["rangs"][1], d["rangs"][0]
    nuage.synchroniser(occ, d)
    assert set(occ.groups[employe]) == {"alice", "rob"} and set(occ.groups[adherent]) == {"alice", "rob"}
    assert occ.groups[gerant] == ["alice"]
    # Rangs désactivés : plus personne dans les groupes de rang (les rangs restent notés).
    d["rangs_actif"] = False
    nuage.synchroniser(occ, d)
    assert all(not occ.groups[r] for r in (adherent, employe, gerant)) and d["rang_de"]["alice"] == gerant


def test_supprimer_un_rang_fait_descendre(occ):
    d = nuage.lire()
    d["rangs_actif"] = True
    bas = nuage.creer_rang(occ, d, "Bas")
    haut = nuage.creer_rang(occ, d, "Haut")
    d["rang_de"] = {"alice": haut, "rob": bas}
    nuage.supprimer_rang(occ, d, haut)
    assert d["rang_de"] == {"alice": bas, "rob": bas} and haut not in occ.groups
    nuage.supprimer_rang(occ, d, bas)
    assert d["rang_de"] == {}


def test_rang_en_double_refuse(occ):
    d = nuage.lire()
    nuage.creer_rang(occ, d, "Adhérent")
    with pytest.raises(nuage.Erreur):
        nuage.creer_rang(occ, d, "adhérent")


def test_groupes_proteges(occ):
    for g in ("admin", nuage.GROUPE_TOUS, "rang-abc123"):
        with pytest.raises(nuage.Erreur):
            nuage.supprimer_groupe(occ, g)
    with pytest.raises(nuage.Erreur):
        nuage.nouveau_groupe(occ, "rang-pirate")


def test_modes_des_dossiers():
    assert nuage.MODES["lecture"][0] == ["read"]
    assert "delete" not in nuage.MODES["depot"][0] and "share" not in nuage.MODES["depot"][0]
    assert nuage.NOMBRES_MODES[1] == "lecture" and nuage.NOMBRES_MODES[7] == "depot" and nuage.NOMBRES_MODES[31] == "complet"
