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
_CACHE_SOURCE = {"t": 0, "v": None}
EJECTES = {}          # nom du disque -> moment de l'éjection
PAUSE_EJECTION = 10 * 60


def source():
    """« nextcloud » si Nextcloud est activé et relié, sinon « disque » (lecture directe du serveur)."""
    if config.DEMO:
        return os.environ.get("JABBA_DEMO_SOURCE", "nextcloud")
    if not (config.NC_USER and config.NC_PASS):
        return "disque"
    if time.time() - _CACHE_SOURCE["t"] < 15:
        return _CACHE_SOURCE["v"]
    code, sortie, _ = commands.hote(["docker", "inspect", "-f", "{{.State.Running}}", "nextcloud"], timeout=10)
    v = "nextcloud" if code == 0 and sortie.strip() == "true" else "disque"
    _CACHE_SOURCE.update(t=time.time(), v=v)
    return v


def ejecte_recemment(nom):
    return time.time() - EJECTES.get(nom, 0) < PAUSE_EJECTION


def reveiller_disques():
    """Un accès réveille l'ouverture automatique des disques branchés (sauf ceux qu'on vient d'éjecter)."""
    if config.DEMO:
        return
    for nom, chemin in disques_connus().items():
        if re.fullmatch(r"/[\w./-]+", chemin) and not ejecte_recemment(nom) and disque_present(chemin):
            ouvrir_disque(chemin)


_OUVERTS = set()   # disques vus ouverts au dernier passage


def ouvrir_disque(chemin):
    """Ouvre un disque branché. Si l'ouverture automatique n'est pas armée (réglage
    modifié sans redémarrage, disque rebranché…), l'appli la réarme elle-même.
    Quand un disque vient d'être (re)branché, Nextcloud refait son inventaire."""
    ok = _ouvrir(chemin)
    if ok and chemin not in _OUVERTS:
        threading.Thread(target=actualiser_nextcloud, args=(chemin,), daemon=True).start()
    (_OUVERTS.add if ok else _OUVERTS.discard)(chemin)
    return ok


def actualiser_nextcloud(chemin):
    """Nextcloud garde en mémoire le contenu d'un disque, même vide (disque débranché) :
    on lui fait relire le disque et on remet son réglage lecture seule / écriture."""
    occ = ["docker", "exec", "-u", "www-data", "nextcloud", "php", "occ"]
    code, sortie, _ = commands.hote(occ + ["files_external:list", "--output=json"], timeout=60)
    if code != 0:
        return   # Nextcloud arrêté ou absent
    try:
        montages = json.loads(sortie)
    except ValueError:
        return
    code, sortie, _ = commands.hote(occ + ["user:list", "--output=json"], timeout=60)
    try:
        utilisateurs = list(json.loads(sortie)) if code == 0 else []
    except ValueError:
        utilisateurs = []
    dossier = os.path.basename(chemin.rstrip("/"))
    for m in montages:
        if (m.get("configuration") or {}).get("datadir", "").rstrip("/") != f"/mnt/disques/{dossier}":
            continue
        commands.hote(occ + ["files_external:option", str(m["mount_id"]), "readonly",
                    "false" if mode_ecriture(chemin) else "true"], timeout=60)
        point = m.get("mount_point", "").strip("/")
        for u in m.get("applicable_users") or utilisateurs:
            commands.hote(occ + ["files:scan", f"--path=/{u}/files/{point}"], timeout=3600)
    activity_log.noter("disque", f"Nextcloud a relu {dossier}")


def _ouvrir(chemin):
    commands.hote(["ls", chemin], timeout=10)
    if commands.hote(["mountpoint", "-q", chemin], timeout=10)[0] == 0:
        return True
    _, unite, _ = commands.hote(["systemd-escape", "--path", "--suffix=automount", chemin])
    unite = unite.strip()
    commands.hote(["sh", "-c", f"systemctl daemon-reload; systemctl reset-failed '{unite[:-10]}.mount' 2>/dev/null; "
                      f"systemctl restart '{unite}'; ls '{chemin}' >/dev/null 2>&1"], timeout=30)
    return commands.hote(["mountpoint", "-q", chemin], timeout=10)[0] == 0


def mode_ecriture(chemin):
    """True si le disque monté sur `chemin` est déclaré en écriture dans /etc/fstab."""
    if config.DEMO:
        return bool(os.environ.get("JABBA_DEMO_ECRITURE"))
    code, fstab, _ = commands.hote(["cat", "/etc/fstab"])
    for ligne in fstab.splitlines():
        champs = ligne.split()
        if len(champs) >= 4 and not ligne.lstrip().startswith("#") and champs[1] == chemin:
            return "ro" not in champs[3].split(",")
    return False


def disques_fs():
    """Pour l'écran Fichiers en mode disque : disques branchés (nom -> chemin) et ceux en lecture seule."""
    branches = disques_branches()
    connus = disques_connus()
    disques = {n: connus[n] for n in branches if n in connus}
    lecture_seule = {n for n, c in disques.items() if not mode_ecriture(c)}
    return disques, lecture_seule


def fs(fonction):
    try:
        return fonction()
    except disk_browser.Refus as e:
        raise HTTPException(e.code, e.message)
    except PermissionError:
        raise HTTPException(403, "Le serveur refuse : droits insuffisants sur ce dossier.")


def disques_env():
    res = {}
    for morceau in os.environ.get("DISQUES", "").split(","):
        if "=" in morceau:
            nom, chemin = morceau.split("=", 1)
            res[nom.strip()] = chemin.strip()
    return res


def disques_connus():
    """Disques externes : « nom -> chemin sur le serveur ». Ceux du .env, puis ceux ajoutés tout seuls."""
    res = disques_env()
    if not config.DEMO:
        for nom, chemin in auto_disks.connus().items():
            res.setdefault(nom, chemin)
    return res


_CACHE_UUID = {"t": 0, "v": {}}


def uuid_disque(chemin):
    """Identifiant du disque monté sur `chemin`, lu dans /etc/fstab (gardé 1 minute)."""
    if time.time() - _CACHE_UUID["t"] > 60:
        _, fstab, _ = commands.hote(["cat", "/etc/fstab"])
        v = {}
        for ligne in fstab.splitlines():
            champs = ligne.split()
            if len(champs) >= 2 and not ligne.lstrip().startswith("#") and champs[0].upper().startswith("UUID="):
                v[champs[1]] = champs[0].split("=", 1)[1].strip('"')
        _CACHE_UUID.update(t=time.time(), v=v)
    return _CACHE_UUID["v"].get(chemin)


def disque_present(chemin):
    """Branché ? Vérifié sans réveiller l'ouverture automatique (qui attendrait 5 s si débranché)."""
    return auto_disks.present(commands.hote, uuid_disque(chemin))


def disques_branches():
    """Noms des disques externes actuellement branchés."""
    if config.DEMO:
        return {"R2-D2"}
    branches = set()
    for nom, chemin in disques_connus().items():
        if not re.fullmatch(r"/[\w./-]+", chemin) or ejecte_recemment(nom) or not disque_present(chemin):
            continue
        if ouvrir_disque(chemin):
            branches.add(nom)
    return branches


# ---------------------------------------------------------------- disques : mode d'accès et éjection
def chemin_disque(nom):
    chemin = disques_connus().get(nom)
    if not chemin or not re.fullmatch(r"/[\w./-]+", chemin):
        raise HTTPException(404, "Disque inconnu")
    return chemin
