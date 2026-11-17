"""D'où viennent les fichiers de l'écran Fichiers : Nextcloud, ou les disques directement.

Quand Nextcloud tourne, l'écran Fichiers passe par lui. Sinon, il lit les dossiers du
serveur (services/disk_browser.py). Ce fichier gère aussi l'ouverture des disques branchés.

Sommaire :
  files_source : « nextcloud » ou « disque »
  wake_disks, open_disk, ejected_recently, rescan_in_nextcloud, is_writable : disques branchés
  disk_params, run_disk_action : appeler disk_browser avec les bons réglages
  configured_disks, known_disks, disk_uuid, disk_is_present, plugged_disks, disk_path :
      quels disques existent et où ils sont montés
"""

import json
import os
import re
import threading
import time

from fastapi import HTTPException

import config
from host import actions, commands
from services import activity_log, auto_disks, disk_browser

# ---------------------------------------------------------------- source de l'écran Fichiers
_SOURCE_CACHE = {"t": 0, "v": None}
EJECTED = {}  # nom du disque -> moment de l'éjection
EJECTION_PAUSE = 10 * 60


def files_source():
    """« nextcloud » si Nextcloud est activé et relié, sinon « disque » (lecture directe du serveur)."""
    if config.DEMO:
        return os.environ.get("JABBA_DEMO_SOURCE", "nextcloud")
    if not (config.NC_USER and config.NC_APP_PASSWORD):
        return "disque"
    if time.time() - _SOURCE_CACHE["t"] < 15:
        return _SOURCE_CACHE["v"]
    code, output, _ = commands.run_action("conteneur_actif", nom="nextcloud")
    value = "nextcloud" if code == 0 and output.strip() == "true" else "disque"
    _SOURCE_CACHE.update(t=time.time(), v=value)
    return value


def ejected_recently(name):
    return time.time() - EJECTED.get(name, 0) < EJECTION_PAUSE


def wake_disks():
    """Un accès réveille l'ouverture automatique des disques branchés (sauf ceux qu'on vient d'éjecter)."""
    if config.DEMO:
        return
    for name, path in known_disks().items():
        if re.fullmatch(r"/[\w./-]+", path) and not ejected_recently(name) and disk_is_present(path):
            open_disk(path)


_OPENED = set()  # disques vus ouverts au dernier passage


def open_disk(path):
    """Ouvre un disque branché. Si l'ouverture automatique n'est pas armée (réglage
    modifié sans redémarrage, disque rebranché…), l'appli la réarme elle-même.
    Quand un disque vient d'être (re)branché, Nextcloud refait son inventaire."""
    mounted = _mount(path)
    if mounted and path not in _OPENED:
        threading.Thread(target=rescan_in_nextcloud, args=(path,), daemon=True).start()
    (_OPENED.add if mounted else _OPENED.discard)(path)
    return mounted


def rescan_in_nextcloud(path):
    """Nextcloud garde en mémoire le contenu d'un disque, même vide (disque débranché) :
    on lui fait relire le disque et on remet son réglage lecture seule / écriture.
    Seulement si le disque est bien ouvert : relu vide, Nextcloud oublierait ses fichiers et leurs partages."""
    if not _mount(path):
        return
    code, output, _ = commands.occ(["files_external:list", "--output=json"])
    if code != 0:
        return  # Nextcloud arrêté ou absent
    try:
        mounts = json.loads(output)
    except ValueError:
        return
    code, output, _ = commands.occ(["user:list", "--output=json"])
    try:
        users = list(json.loads(output)) if code == 0 else []
    except ValueError:
        users = []
    folder = os.path.basename(path.rstrip("/"))
    for mount in mounts:
        if (mount.get("configuration") or {}).get("datadir", "").rstrip("/") != f"/mnt/disques/{folder}":
            continue
        readonly = "false" if is_writable(path) else "true"
        commands.occ(["files_external:option", str(mount["mount_id"]), "readonly", readonly])
        mount_point = mount.get("mount_point", "").strip("/")
        for user in mount.get("applicable_users") or users:
            commands.occ(["files:scan", f"--path=/{user}/files/{mount_point}"], timeout=3600)
    activity_log.record("disque", f"Nextcloud a relu {folder}")


def is_mounted(path):
    """Vrai si un disque est monté sur `path` (action « est_monte » de la liste fermée)."""
    try:
        return commands.run_action("est_monte", chemin=path)[0] == 0
    except actions.ActionError:
        return False


def _mount(path):
    """Ouvre le disque (un accès réveille l'ouverture automatique), sinon la réarme."""
    try:
        commands.run_action("reveiller_disque", chemin=path)
        if is_mounted(path):
            return True
        commands.run_action("rearmer_disque", chemin=path)
    except actions.ActionError:
        return False
    return is_mounted(path)


def is_writable(path):
    """True si le disque monté sur `chemin` est déclaré en écriture dans /etc/fstab."""
    if config.DEMO:
        return bool(os.environ.get("JABBA_DEMO_ECRITURE"))
    code, fstab, _ = commands.run_action("lire_fstab")
    for line in fstab.splitlines():
        fields = line.split()
        if len(fields) >= 4 and not line.lstrip().startswith("#") and fields[1] == path:
            return "ro" not in fields[3].split(",")
    return False


def disk_params():
    """Pour l'écran Fichiers en mode disque : disques branchés (nom -> chemin) et ceux en lecture seule."""
    plugged = plugged_disks()
    known = known_disks()
    disks = {name: known[name] for name in plugged if name in known}
    read_only = {name for name, mount_path in disks.items() if not is_writable(mount_path)}
    return disks, read_only


def run_disk_action(function):
    try:
        return function()
    except disk_browser.Refusal as error:
        raise HTTPException(error.code, error.message)
    except PermissionError:
        raise HTTPException(403, "Le serveur refuse : droits insuffisants sur ce dossier.")


def configured_disks():
    result = {}
    for share in os.environ.get("DISQUES", "").split(","):
        if "=" in share:
            name, path = share.split("=", 1)
            result[name.strip()] = path.strip()
    return result


def known_disks():
    """Disques externes : « nom -> chemin sur le serveur ». Ceux du .env, puis ceux ajoutés tout seuls."""
    result = configured_disks()
    if not config.DEMO:
        for name, path in auto_disks.known_disks().items():
            result.setdefault(name, path)
    return result


_UUID_CACHE = {"t": 0, "v": {}}


def disk_uuid(path):
    """Identifiant du disque monté sur `chemin`, lu dans /etc/fstab (gardé 1 minute)."""
    if time.time() - _UUID_CACHE["t"] > 60:
        _, fstab, _ = commands.run_action("lire_fstab")
        value = {}
        for line in fstab.splitlines():
            fields = line.split()
            if len(fields) >= 2 and not line.lstrip().startswith("#") and fields[0].upper().startswith("UUID="):
                value[fields[1]] = fields[0].split("=", 1)[1].strip('"')
        _UUID_CACHE.update(t=time.time(), v=value)
    return _UUID_CACHE["v"].get(path)


def disk_is_present(path):
    """Branché ? Vérifié sans réveiller l'ouverture automatique (qui attendrait 5 s si débranché)."""
    return auto_disks.is_present(disk_uuid(path))


def plugged_disks():
    """Noms des disques externes actuellement branchés."""
    if config.DEMO:
        return {"R2-D2"}
    plugged = set()
    for name, path in known_disks().items():
        if not re.fullmatch(r"/[\w./-]+", path) or ejected_recently(name) or not disk_is_present(path):
            continue
        if open_disk(path):
            plugged.add(name)
    return plugged


# ---------------------------------------------------------------- disques : mode d'accès et éjection
def disk_path(name):
    path = known_disks().get(name)
    if not path or not re.fullmatch(r"/[\w./-]+", path):
        raise HTTPException(404, "Disque inconnu")
    return path
