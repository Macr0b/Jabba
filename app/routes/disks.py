"""API des disques branchés au serveur : liste, lecture seule ou écriture, éjection.

Sommaire :
  GET  /api/disques                 list_disks
  POST /api/disques/{nom}/mode      set_disk_mode
  POST /api/disques/{nom}/ejecter   eject_disk
  POST /api/disques/{nom}/oublier   forget_disk
  POST /api/disques/reprendre       resume_disk
"""

import json
import re
import threading
import time

from fastapi import APIRouter, HTTPException, Request

import config
from host import commands
from security import guard
from services import activity_log, auto_disks, files_source, measurements

router = APIRouter()


# ---------------------------------------------------------------- disques
@router.get("/api/disques")
def list_disks():
    if config.DEMO:
        from services.demo_data import DISKS

        return DISKS
    files_source.wake_disks()
    code, output, error_output = commands.run_on_host(
        ["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINT,RM,TRAN,TYPE,MODEL"]
    )
    if code != 0:
        raise HTTPException(500, error_output[:200])
    tree = json.loads(output)["blockdevices"]
    mounted = []
    for device in tree:
        for partition in device.get("children") or []:
            if partition.get("mountpoint"):
                mounted.append(partition["mountpoint"])
    usage = measurements.disk_space([mount for mount in mounted if mount.startswith("/")]) if mounted else {}
    roles = {"/": "Système", "/boot/efi": "Démarrage", "/srv/data": "Données"}
    known = {path: name for name, path in files_source.known_disks().items()}
    auto = {disk["chemin"] for disk in auto_disks.read()["disques"]}
    forgotten = set(auto_disks.read()["oublies"])
    result = []
    for device in tree:
        if device.get("type") != "disk" or device["name"].startswith(("loop", "zram", "sr")):
            continue
        shares = []
        for partition in device.get("children") or []:
            if partition.get("fstype") in (None, "swap") and not partition.get("mountpoint"):
                continue
            if (
                partition.get("mountpoint")
                and partition["mountpoint"].startswith("/boot/")
                and partition["mountpoint"] != "/boot/efi"
            ):
                continue
            mountpoint = partition.get("mountpoint")
            shares.append(
                {
                    "nom": partition["name"],
                    "taille": partition["size"],
                    "type": partition.get("fstype"),
                    "etiquette": partition.get("label"),
                    "montage": mountpoint,
                    "role": (
                        "Disque externe"
                        if mountpoint in known
                        else roles.get(mountpoint, "Non utilisé" if not mountpoint else mountpoint)
                    ),
                    "usage": usage.get(mountpoint),
                    "disque_jabba": {
                        "nom": known[mountpoint],
                        "ecriture": files_source.is_writable(mountpoint),
                        "auto": mountpoint in auto,
                    }
                    if mountpoint in known
                    else None,
                    "oublie": (partition.get("uuid") or "").lower()
                    if (partition.get("uuid") or "").lower() in forgotten and not mountpoint
                    else None,
                    "illisible": auto_disks.UNREADABLE.get((partition.get("fstype") or "").lower()),
                }
            )
        result.append(
            {
                "nom": device["name"],
                "modele": (device.get("model") or "").strip(),
                "taille": device["size"],
                "externe": bool(device.get("rm")) or device.get("tran") == "usb",
                "partitions": shares,
            }
        )
    return result


@router.post("/api/disques/{nom}/mode")
async def set_disk_mode(nom: str, request: Request):
    guard.require_admin(request)
    path = files_source.disk_path(nom)
    writable = bool((await request.json()).get("ecriture"))
    if config.DEMO:
        return {"ok": True, "ecriture": writable}
    code, fstab, _ = commands.run_action("lire_fstab")
    lines, found = [], False
    for line in fstab.splitlines():
        fields = line.split()
        if len(fields) >= 4 and not line.lstrip().startswith("#") and fields[1] == path:
            options = [options_list for options_list in fields[3].split(",") if options_list not in ("ro", "rw")]
            fields[3] = ",".join(["rw" if writable else "ro"] + options)
            line = " ".join(fields)
            found = True
        lines.append(line)
    if not found:
        raise HTTPException(404, "Ce disque n'est pas déclaré sur le serveur.")
    code, _, error_output = commands.run_on_host_with_input(
        ["sh", "-c", "cp /etc/fstab /etc/fstab.jabba-bak && cat > /etc/fstab"], "\n".join(lines) + "\n"
    )
    if code != 0:
        raise HTTPException(500, "Impossible d'enregistrer le réglage : " + error_output[:200])
    _, unit, _ = commands.run_on_host(["systemd-escape", "--path", "--suffix=automount", path])
    commands.run_shell_on_host(
        f"systemctl daemon-reload; umount '{path}' 2>/dev/null; systemctl restart '{unit.strip()}'; ls '{path}' >/dev/null 2>&1",
        timeout=30,
    )
    # Même réglage côté Nextcloud, s'il est activé.
    code, output, _ = commands.run_on_host(
        ["docker", "exec", "-u", "www-data", "nextcloud", "php", "occ", "files_external:list", "--output=json"],
        timeout=60,
    )
    if code == 0:
        try:
            for mount in json.loads(output):
                if mount.get("mount_point", "").strip("/") == nom:
                    commands.run_on_host(
                        [
                            "docker",
                            "exec",
                            "-u",
                            "www-data",
                            "nextcloud",
                            "php",
                            "occ",
                            "files_external:option",
                            str(mount["mount_id"]),
                            "readonly",
                            "false" if writable else "true",
                        ],
                        timeout=60,
                    )
        except ValueError:
            pass
    activity_log.record("disque", f"{nom} : {'écriture autorisée' if writable else 'lecture seule'}", request.state.nom)
    return {"ok": True, "ecriture": writable}


@router.post("/api/disques/{nom}/ejecter")
def eject_disk(nom: str, request: Request):
    path = files_source.disk_path(nom)
    if config.DEMO:
        files_source.EJECTED[nom] = time.time()
        return {"ok": True}
    code, _, error_output = commands.run_action("ejecter", chemin=path)
    if code != 0 and "not mounted" not in error_output:
        raise HTTPException(
            409,
            "Le disque est en cours d'utilisation (lecture d'un film, copie…). Arrête ce qui l'utilise et réessaie.",
        )
    files_source.EJECTED[nom] = time.time()
    activity_log.record("disque", f"{nom} éjecté", request.state.nom)
    return {"ok": True}


@router.post("/api/disques/{nom}/oublier")
def forget_disk(nom: str, request: Request):
    guard.require_admin(request, strong=True)
    if config.DEMO:
        return {"ok": True}
    try:
        disk = auto_disks.forget(commands.run_on_host, commands.run_on_host_with_input, nom)
    except KeyError:
        raise HTTPException(404, "Seuls les disques ajoutés automatiquement peuvent être oubliés ici.")
    activity_log.record("disque", f"{disk['nom']} oublié : il ne sera plus ajouté tout seul", request.state.nom)
    return {"ok": True}


@router.post("/api/disques/reprendre")
async def resume_disk(request: Request):
    guard.require_admin(request)
    disk_uuid = str((await request.json()).get("uuid", ""))
    if not re.fullmatch(r"[\w-]{4,64}", disk_uuid):
        raise HTTPException(400, "Disque invalide")
    if not config.DEMO:
        auto_disks.resume(disk_uuid)
        threading.Thread(target=auto_disks.scan_disks, daemon=True).start()
    activity_log.record("disque", "Disque remis en ajout automatique", request.state.nom)
    return {"ok": True}
