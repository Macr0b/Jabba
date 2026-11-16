"""Dossiers partagés avec un rang ou un groupe : un dossier de disque (stockage externe) ou de Nextcloud (partage)."""

import json

import pytest

from services import nextcloud_accounts, nextcloud_shares


class FauxNextcloud:
    """Imite occ (stockages externes, groupes) et l'API de partage de Nextcloud."""

    def __init__(self):
        self.mounts = [
            {
                "mount_id": 1,
                "mount_point": "/R2-D2",
                "configuration": {"datadir": "/mnt/disques/R2-D2"},
                "applicable_users": [],
                "applicable_groups": ["admin"],
                "options": {},
            },
        ]
        self.groups = {"admin", "membres", "rang-abc123"}
        self.shares = {}
        self.calls = []
        self.fail_on = None

    def occ(self, args, input_text=None, is_password=False, timeout=60):
        self.calls.append(args)
        command = args[0]
        if command == self.fail_on:
            return 1, "", "erreur simulée"
        if command == "files_external:list":
            return 0, json.dumps(self.mounts), ""
        if command == "group:info":
            return (0, "", "") if args[1] in self.groups else (1, "", "")
        if command == "files_external:create":
            new_id = max(m["mount_id"] for m in self.mounts) + 1
            datadir = args[args.index("-c") + 1].split("=", 1)[1]
            self.mounts.append(
                {
                    "mount_id": new_id,
                    "mount_point": "/" + args[1],
                    "configuration": {"datadir": datadir},
                    "applicable_users": [],
                    "applicable_groups": [],
                    "options": {},
                }
            )
            return 0, f"Storage created with id {new_id}\n", ""
        mount = lambda mount_id: next(m for m in self.mounts if m["mount_id"] == int(mount_id))
        if command == "files_external:applicable":
            mount(args[-1])["applicable_groups"].append(args[1].split("=", 1)[1])
            return 0, "", ""
        if command == "files_external:option":
            mount(args[1])["options"][args[2]] = args[3]
            return 0, "", ""
        if command == "files_external:delete":
            self.mounts = [m for m in self.mounts if m["mount_id"] != int(args[-1])]
            return 0, "", ""
        return 1, "", "commande inconnue"

    def ocs(self, method, url_path, data):
        if method == "POST":
            share_id = str(len(self.shares) + 1)
            self.shares[share_id] = data
            return {"id": share_id}
        if method == "DELETE":
            self.shares.pop(url_path.strip("/"))
            return {}
        raise AssertionError(method)


@pytest.fixture
def nc():
    return FauxNextcloud()


def test_dossier_d_un_disque_partage_seul(nc):
    """Un sous-dossier de R2-D2 devient un stockage à part : le groupe ne voit que lui, en lecture seule."""
    state = {}
    share = nextcloud_shares.share_folder(nc.occ, nc.ocs, state, "/R2-D2/Projets/Bois", "rang-abc123", "lecture")
    created = nc.mounts[-1]
    assert created["configuration"]["datadir"] == "/mnt/disques/R2-D2/Projets/Bois"
    assert created["mount_point"] == "/Bois" and created["applicable_groups"] == ["rang-abc123"]
    assert created["options"] == {"readonly": "true", "enable_sharing": "false"}
    assert share["type"] == "stockage" and state["partages"] == [share]
    assert nc.mounts[0]["applicable_groups"] == ["admin"]  # le disque entier reste aux admins


def test_ecriture_si_demandee(nc):
    nextcloud_shares.share_folder(nc.occ, nc.ocs, {}, "/R2-D2/Commun", "membres", "ecriture")
    assert nc.mounts[-1]["options"]["readonly"] == "false"


def test_nom_deja_pris(nc):
    state = {}
    nextcloud_shares.share_folder(nc.occ, nc.ocs, state, "/R2-D2/A/Photos", "membres", "lecture")
    nextcloud_shares.share_folder(nc.occ, nc.ocs, state, "/R2-D2/B/Photos", "membres", "lecture")
    assert [m["mount_point"] for m in nc.mounts[1:]] == ["/Photos", "/Photos (2)"]


def test_dossier_nextcloud_partage_ordinaire(nc):
    state = {}
    share = nextcloud_shares.share_folder(nc.occ, nc.ocs, state, "/Documents/Fablab", "membres", "ecriture")
    assert share["type"] == "partage"
    assert nc.shares[share["share_id"]] == {
        "path": "/Documents/Fablab",
        "shareType": 1,
        "shareWith": "membres",
        "permissions": 15,
    }
    nextcloud_shares.stop_sharing(nc.occ, nc.ocs, state, share["id"])
    assert nc.shares == {} and state["partages"] == []


def test_arreter_un_partage_de_disque(nc):
    state = {}
    share = nextcloud_shares.share_folder(nc.occ, nc.ocs, state, "/R2-D2/Projets", "membres", "lecture")
    nextcloud_shares.stop_sharing(nc.occ, nc.ocs, state, share["id"])
    assert [m["mount_id"] for m in nc.mounts] == [1] and state["partages"] == []


def test_partage_jamais_a_moitie_fait(nc):
    """Si Nextcloud refuse de réserver le stockage au groupe, il est retiré : jamais ouvert à tous."""
    nc.fail_on = "files_external:applicable"
    with pytest.raises(nextcloud_accounts.CloudError):
        nextcloud_shares.share_folder(nc.occ, nc.ocs, {}, "/R2-D2/Projets", "membres", "lecture")
    assert [m["mount_id"] for m in nc.mounts] == [1]


@pytest.mark.parametrize("chemin", ["", "/", "/R2-D2/../etc", "/../R2-D2", "/R2-D2/a/../../b"])
def test_chemins_refuses(nc, chemin):
    with pytest.raises(nextcloud_accounts.CloudError):
        nextcloud_shares.share_folder(nc.occ, nc.ocs, {}, chemin, "membres", "lecture")
    assert len(nc.mounts) == 1 and nc.shares == {}


def test_groupe_et_mode_verifies(nc):
    with pytest.raises(nextcloud_accounts.CloudError):
        nextcloud_shares.share_folder(nc.occ, nc.ocs, {}, "/R2-D2/Projets", "inconnu", "lecture")
    with pytest.raises(nextcloud_accounts.CloudError):
        nextcloud_shares.share_folder(nc.occ, nc.ocs, {}, "/R2-D2/Projets", "membres", "tout")


def test_partage_inconnu(nc):
    with pytest.raises(nextcloud_accounts.CloudError):
        nextcloud_shares.stop_sharing(nc.occ, nc.ocs, {}, "abcd")


def test_les_disques_partages_ne_sont_pas_rendus_aux_admins(nc):
    """La surveillance « disques réservés aux admins » ne touche pas un dossier partagé avec un groupe."""
    nextcloud_shares.share_folder(nc.occ, nc.ocs, {}, "/R2-D2/Projets", "membres", "lecture")
    nextcloud_accounts.reserve_disks_for_admins(nc.occ)
    assert nc.mounts[-1]["applicable_groups"] == ["membres"]
