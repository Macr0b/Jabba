"""services.files_source"""
import json
import os
import re
import threading
import time
from services import disk_browser
from services import auto_disks
from services import activity_log
from fastapi import HTTPException
import config
from host import commands


# ---------------------------------------------------------------- source de l'écran Fichiers
_SOURCE_CACHE = {"t": 0, "v": None}
EJECTED = {}          # nom du disque -> moment de l'éjection
EJECTION_PAUSE = 10 * 60


def files_source():
    """« nextcloud » si Nextcloud est activé et relié, sinon « disque » (lecture directe du serveur)."""
    if config.DEMO:
        return os.environ.get("JABBA_DEMO_SOURCE", "nextcloud")
    if not (config.NC_USER and config.NC_APP_PASSWORD):
        return "disque"
    if time.time() - _SOURCE_CACHE["t"] < 15:
        return _SOURCE_CACHE["v"]
    code, sortie, _ = commands.run_on_host(["docker", "inspect", "-f", "{{.State.Running}}", "nextcloud"], timeout=10)
    v = "nextcloud" if code == 0 and sortie.strip() == "true" else "disque"
    _SOURCE_CACHE.update(t=time.time(), v=v)
    return v


def ejected_recently(nom):
    return time.time() - EJECTED.get(nom, 0) < EJECTION_PAUSE


def wake_disks():
    """Un accès réveille l'ouverture automatique des disques branchés (sauf ceux qu'on vient d'éjecter)."""
    if config.DEMO:
        return
    for nom, chemin in known_disks().items():
        if re.fullmatch(r"/[\w./-]+", chemin) and not ejected_recently(nom) and disk_is_present(chemin):
            open_disk(chemin)


_OPENED = set()   # disques vus ouverts au dernier passage


def open_disk(chemin):
    """Ouvre un disque branché. Si l'ouverture automatique n'est pas armée (réglage
    modifié sans redémarrage, disque rebranché…), l'appli la réarme elle-même.
    Quand un disque vient d'être (re)branché, Nextcloud refait son inventaire."""
    ok = _mount(chemin)
    if ok and chemin not in _OPENED:
        threading.Thread(target=rescan_in_nextcloud, args=(chemin,), daemon=True).start()
    (_OPENED.add if ok else _OPENED.discard)(chemin)
    return ok


def rescan_in_nextcloud(chemin):
    """Nextcloud garde en mémoire le contenu d'un disque, même vide (disque débranché) :
    on lui fait relire le disque et on remet son réglage lecture seule / écriture."""
    occ = ["docker", "exec", "-u", "www-data", "nextcloud", "php", "occ"]
    code, sortie, _ = commands.run_on_host(occ + ["files_external:list", "--output=json"], timeout=60)
    if code != 0:
        return   # Nextcloud arrêté ou absent
    try:
        montages = json.loads(sortie)
    except ValueError:
        return
    code, sortie, _ = commands.run_on_host(occ + ["user:list", "--output=json"], timeout=60)
    try:
        utilisateurs = list(json.loads(sortie)) if code == 0 else []
    except ValueError:
        utilisateurs = []
    dossier = os.path.basename(chemin.rstrip("/"))
    for m in montages:
        if (m.get("configuration") or {}).get("datadir", "").rstrip("/") != f"/mnt/disques/{dossier}":
            continue
        commands.run_on_host(occ + ["files_external:option", str(m["mount_id"]), "readonly",
                    "false" if is_writable(chemin) else "true"], timeout=60)
        point = m.get("mount_point", "").strip("/")
        for u in m.get("applicable_users") or utilisateurs:
            commands.run_on_host(occ + ["files:scan", f"--path=/{u}/files/{point}"], timeout=3600)
    activity_log.record("disque", f"Nextcloud a relu {dossier}")


def _mount(chemin):
    commands.run_on_host(["ls", chemin], timeout=10)
    if commands.run_on_host(["mountpoint", "-q", chemin], timeout=10)[0] == 0:
        return True
    _, unite, _ = commands.run_on_host(["systemd-escape", "--path", "--suffix=automount", chemin])
    unite = unite.strip()
    commands.run_on_host(["sh", "-c", f"systemctl daemon-reload; systemctl reset-failed '{unite[:-10]}.mount' 2>/dev/null; "
                      f"systemctl restart '{unite}'; ls '{chemin}' >/dev/null 2>&1"], timeout=30)
    return commands.run_on_host(["mountpoint", "-q", chemin], timeout=10)[0] == 0


def is_writable(chemin):
    """True si le disque monté sur `chemin` est déclaré en écriture dans /etc/fstab."""
    if config.DEMO:
        return bool(os.environ.get("JABBA_DEMO_ECRITURE"))
    code, fstab, _ = commands.run_on_host(["cat", "/etc/fstab"])
    for ligne in fstab.splitlines():
        champs = ligne.split()
        if len(champs) >= 4 and not ligne.lstrip().startswith("#") and champs[1] == chemin:
            return "ro" not in champs[3].split(",")
    return False


def disk_params():
    """Pour l'écran Fichiers en mode disque : disques branchés (nom -> chemin) et ceux en lecture seule."""
    branches = plugged_disks()
    connus = known_disks()
    disques = {n: connus[n] for n in branches if n in connus}
    lecture_seule = {n for n, c in disques.items() if not is_writable(c)}
    return disques, lecture_seule


def run_disk_action(fonction):
    try:
        return fonction()
    except disk_browser.Refusal as e:
        raise HTTPException(e.code, e.message)
    except PermissionError:
        raise HTTPException(403, "Le serveur refuse : droits insuffisants sur ce dossier.")


def configured_disks():
    res = {}
    for morceau in os.environ.get("DISQUES", "").split(","):
        if "=" in morceau:
            nom, chemin = morceau.split("=", 1)
            res[nom.strip()] = chemin.strip()
    return res


def known_disks():
    """Disques externes : « nom -> chemin sur le serveur ». Ceux du .env, puis ceux ajoutés tout seuls."""
    res = configured_disks()
    if not config.DEMO:
        for nom, chemin in auto_disks.known_disks().items():
            res.setdefault(nom, chemin)
    return res


_UUID_CACHE = {"t": 0, "v": {}}


def disk_uuid(chemin):
    """Identifiant du disque monté sur `chemin`, lu dans /etc/fstab (gardé 1 minute)."""
    if time.time() - _UUID_CACHE["t"] > 60:
        _, fstab, _ = commands.run_on_host(["cat", "/etc/fstab"])
        v = {}
        for ligne in fstab.splitlines():
            champs = ligne.split()
            if len(champs) >= 2 and not ligne.lstrip().startswith("#") and champs[0].upper().startswith("UUID="):
                v[champs[1]] = champs[0].split("=", 1)[1].strip('"')
        _UUID_CACHE.update(t=time.time(), v=v)
    return _UUID_CACHE["v"].get(chemin)


def disk_is_present(chemin):
    """Branché ? Vérifié sans réveiller l'ouverture automatique (qui attendrait 5 s si débranché)."""
    return auto_disks.is_present(commands.run_on_host, disk_uuid(chemin))


def plugged_disks():
    """Noms des disques externes actuellement branchés."""
    if config.DEMO:
        return {"R2-D2"}
    branches = set()
    for nom, chemin in known_disks().items():
        if not re.fullmatch(r"/[\w./-]+", chemin) or ejected_recently(nom) or not disk_is_present(chemin):
            continue
        if open_disk(chemin):
            branches.add(nom)
    return branches


# ---------------------------------------------------------------- disques : mode d'accès et éjection
def disk_path(nom):
    chemin = known_disks().get(nom)
    if not chemin or not re.fullmatch(r"/[\w./-]+", chemin):
        raise HTTPException(404, "Disque inconnu")
    return chemin
