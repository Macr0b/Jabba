"""Dossiers partagés avec un rang ou un groupe, depuis l'écran Fichiers (façon « Partager avec… »).

Deux cas, selon où se trouve le dossier :
  - dans un disque branché au serveur (R2-D2, Films et séries…) : Nextcloud interdit d'y partager,
    et c'est voulu (sinon on pourrait repartager tout le disque). Jabba crée alors un « stockage
    externe » qui montre CE dossier seulement, réservé au groupe choisi, en lecture seule ou non ;
  - dans les fichiers Nextcloud du compte relié à Jabba : un partage Nextcloud ordinaire avec le groupe.

Les fichiers ne bougent jamais. Arrêter un partage retire l'accès, rien n'est effacé.
Chaque partage est noté dans l'état des comptes Nextcloud (clé « partages »).

Sommaire :
  MODES : « lecture » (voir seulement) ou « ecriture » (voir et modifier)
  external_mounts : les stockages externes de Nextcloud (les disques)
  share_folder : partager un dossier avec un groupe
  stop_sharing : retirer un partage
  list_shares : les partages notés
  nextcloud_ocs : l'API de partage de Nextcloud (partages ordinaires)
"""

import json
import re
import secrets

import httpx

import config
from services import nextcloud_accounts

MODES = {
    "lecture": {"nom": "Voir seulement", "permissions": 1, "readonly": "true"},
    "ecriture": {"nom": "Voir et modifier", "permissions": 15, "readonly": "false"},
}
# Les partages faits par Jabba sont des stockages externes nommés comme le dossier.
NAME_PATTERN = re.compile(r"^[^/\\]{1,80}$")


def external_mounts(occ):
    """Les stockages externes de Nextcloud (liste vide si l'appli est absente)."""
    code, output, _ = occ(["files_external:list", "--output=json"])
    if code != 0:
        return []
    try:
        mounts = json.loads(output)
    except ValueError:
        raise nextcloud_accounts.CloudError("Nextcloud : liste des disques illisible.")
    return mounts if isinstance(mounts, list) else []


def _clean_parts(path):
    parts = [part for part in str(path or "").split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise nextcloud_accounts.CloudError("Dossier invalide.")
    return parts


def _check_group(occ, group):
    if not group or occ(["group:info", group])[0] != 0:
        raise nextcloud_accounts.CloudError("Rang ou groupe inconnu.")


def _free_mount_point(mounts, wanted):
    """Un nom de stockage pas encore pris : « Projets », sinon « Projets (2) »…"""
    taken = {str(mount.get("mount_point", "")).strip("/").lower() for mount in mounts}
    name, counter = wanted, 2
    while name.lower() in taken:
        name, counter = f"{wanted} ({counter})", counter + 1
    return name


def share_folder(occ, ocs, state, path, group, mode):
    """Partage le dossier `path` (chemin de l'écran Fichiers) avec `group`. Renvoie le partage noté.

    `ocs(method, url_path, data)` fait une demande à l'API de partage de Nextcloud et renvoie
    la partie « data » de sa réponse (ou lève CloudError).
    """
    if mode not in MODES:
        raise nextcloud_accounts.CloudError("Mode de partage inconnu.")
    parts = _clean_parts(path)
    _check_group(occ, group)
    mounts = external_mounts(occ)
    disk = next((mount for mount in mounts if str(mount.get("mount_point", "")).strip("/") == parts[0]), None)
    share = {
        "id": secrets.token_hex(4),
        "chemin": "/" + "/".join(parts),
        "groupe": group,
        "mode": mode,
    }
    if disk:
        # Un dossier d'un disque : un stockage externe qui ne montre que lui.
        datadir = str((disk.get("configuration") or {}).get("datadir", "")).rstrip("/")
        if not datadir.startswith("/"):
            raise nextcloud_accounts.CloudError("Ce disque ne se partage pas depuis Jabba.")
        folder = "/".join([datadir] + parts[1:])
        name = _free_mount_point(mounts, parts[-1])
        if not NAME_PATTERN.match(name):
            raise nextcloud_accounts.CloudError("Nom de dossier invalide.")
        output = nextcloud_accounts.occ_or_fail(
            occ, ["files_external:create", name, "local", "null::null", "-c", f"datadir={folder}"]
        )
        match = re.search(r"(\d+)\s*$", output.strip())
        if not match:
            raise nextcloud_accounts.CloudError("Nextcloud n'a pas créé le partage.")
        mount_id = match.group(1)
        try:
            nextcloud_accounts.occ_or_fail(occ, ["files_external:applicable", f"--add-group={group}", mount_id])
            nextcloud_accounts.occ_or_fail(
                occ, ["files_external:option", mount_id, "readonly", MODES[mode]["readonly"]]
            )
            nextcloud_accounts.occ_or_fail(occ, ["files_external:option", mount_id, "enable_sharing", "false"])
        except nextcloud_accounts.CloudError:
            occ(["files_external:delete", "-y", mount_id])  # jamais un partage à moitié fait (ouvert à tous)
            raise
        share.update(type="stockage", mount_id=int(mount_id), nom=name)
    else:
        # Un dossier des fichiers Nextcloud : partage ordinaire avec le groupe.
        data = ocs(
            "POST",
            "",
            {"path": share["chemin"], "shareType": 1, "shareWith": group, "permissions": MODES[mode]["permissions"]},
        )
        share.update(type="partage", share_id=str(data.get("id")), nom=parts[-1])
    state.setdefault("partages", []).append(share)
    return share


def stop_sharing(occ, ocs, state, share_id):
    """Retire un partage noté (l'accès disparaît, les fichiers restent)."""
    shares = state.setdefault("partages", [])
    share = next((item for item in shares if item["id"] == share_id), None)
    if not share:
        raise nextcloud_accounts.CloudError("Partage introuvable.")
    if share["type"] == "stockage":
        code, _, error = occ(["files_external:delete", "-y", str(share["mount_id"])])
        if code != 0 and "not found" not in (error or "").lower():
            raise nextcloud_accounts.CloudError("Nextcloud n'a pas retiré le partage.")
    else:
        ocs("DELETE", f"/{share['share_id']}", None)
    state["partages"] = [item for item in shares if item["id"] != share_id]
    return share


def list_shares(state):
    return list(state.get("partages", []))


def nextcloud_ocs(method, url_path, data):
    """Une demande à l'API de partage de Nextcloud, avec le compte relié à Jabba."""
    url = config.NC_URL + "/ocs/v2.php/apps/files_sharing/api/v1/shares" + url_path + "?format=json"
    headers = {"OCS-APIRequest": "true"}
    if config.NC_HOST:
        headers["Host"] = config.NC_HOST
    try:
        response = httpx.request(
            method, url, data=data, headers=headers, auth=(config.NC_USER, config.NC_APP_PASSWORD), timeout=30
        )
        body = response.json()
    except (httpx.HTTPError, ValueError):
        raise nextcloud_accounts.CloudError("Nextcloud ne répond pas.")
    meta = (body.get("ocs") or {}).get("meta") or {}
    if meta.get("statuscode") not in (100, 200):
        raise nextcloud_accounts.CloudError(meta.get("message") or "Nextcloud a refusé le partage.")
    return (body.get("ocs") or {}).get("data") or {}
