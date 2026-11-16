"""routes.disks"""
import json
import re
import threading
import time
from services import auto_disks
from services import activity_log
from fastapi import HTTPException, Request
import config
from host import commands
from security import guard
from services import files_source
from services import measurements
from fastapi import APIRouter

router = APIRouter()

# ---------------------------------------------------------------- disques
@router.get("/api/disques")
def list_disks():
    if config.DEMO:
        from services.demo_data import DISKS
        return DISKS
    files_source.wake_disks()
    code, sortie, err = commands.run_on_host(["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINT,RM,TRAN,TYPE,MODEL"])
    if code != 0:
        raise HTTPException(500, err[:200])
    arbre = json.loads(sortie)["blockdevices"]
    montes = []
    for d in arbre:
        for p in d.get("children") or []:
            if p.get("mountpoint"):
                montes.append(p["mountpoint"])
    usage = measurements.disk_space([m for m in montes if m.startswith("/")]) if montes else {}
    roles = {"/": "Système", "/boot/efi": "Démarrage", "/srv/data": "Données"}
    connus = {chemin: nom for nom, chemin in files_source.known_disks().items()}
    auto = {x["chemin"] for x in auto_disks.read()["disques"]}
    oublies = set(auto_disks.read()["oublies"])
    res = []
    for d in arbre:
        if d.get("type") != "disk" or d["name"].startswith(("loop", "zram", "sr")):
            continue
        parts = []
        for p in d.get("children") or []:
            if p.get("fstype") in (None, "swap") and not p.get("mountpoint"):
                continue
            if p.get("mountpoint") and p["mountpoint"].startswith("/boot/") and p["mountpoint"] != "/boot/efi":
                continue
            mp = p.get("mountpoint")
            parts.append({
                "nom": p["name"], "taille": p["size"], "type": p.get("fstype"), "etiquette": p.get("label"),
                "montage": mp, "role": ("Disque externe" if mp in connus else roles.get(mp, "Non utilisé" if not mp else mp)),
                "usage": usage.get(mp),
                "disque_jabba": {"nom": connus[mp], "ecriture": files_source.is_writable(mp), "auto": mp in auto} if mp in connus else None,
                "oublie": (p.get("uuid") or "").lower() if (p.get("uuid") or "").lower() in oublies and not mp else None,
                "illisible": auto_disks.UNREADABLE.get((p.get("fstype") or "").lower()),
            })
        res.append({
            "nom": d["name"], "modele": (d.get("model") or "").strip(), "taille": d["size"],
            "externe": bool(d.get("rm")) or d.get("tran") == "usb", "partitions": parts,
        })
    return res


@router.post("/api/disques/{nom}/mode")
async def set_disk_mode(nom: str, request: Request):
    guard.require_admin(request)
    chemin = files_source.disk_path(nom)
    ecriture = bool((await request.json()).get("ecriture"))
    if config.DEMO:
        return {"ok": True, "ecriture": ecriture}
    code, fstab, _ = commands.run_on_host(["cat", "/etc/fstab"])
    lignes, trouve = [], False
    for ligne in fstab.splitlines():
        champs = ligne.split()
        if len(champs) >= 4 and not ligne.lstrip().startswith("#") and champs[1] == chemin:
            options = [o for o in champs[3].split(",") if o not in ("ro", "rw")]
            champs[3] = ",".join(["rw" if ecriture else "ro"] + options)
            ligne = " ".join(champs)
            trouve = True
        lignes.append(ligne)
    if not trouve:
        raise HTTPException(404, "Ce disque n'est pas déclaré sur le serveur.")
    code, _, err = commands.run_on_host_with_input(["sh", "-c", "cp /etc/fstab /etc/fstab.jabba-bak && cat > /etc/fstab"], "\n".join(lignes) + "\n")
    if code != 0:
        raise HTTPException(500, "Impossible d'enregistrer le réglage : " + err[:200])
    _, unite, _ = commands.run_on_host(["systemd-escape", "--path", "--suffix=automount", chemin])
    commands.run_shell_on_host(f"systemctl daemon-reload; umount '{chemin}' 2>/dev/null; systemctl restart '{unite.strip()}'; ls '{chemin}' >/dev/null 2>&1", timeout=30)
    # Même réglage côté Nextcloud, s'il est activé.
    code, sortie, _ = commands.run_on_host(["docker", "exec", "-u", "www-data", "nextcloud", "php", "occ", "files_external:list", "--output=json"], timeout=60)
    if code == 0:
        try:
            for m in json.loads(sortie):
                if m.get("mount_point", "").strip("/") == nom:
                    commands.run_on_host(["docker", "exec", "-u", "www-data", "nextcloud", "php", "occ", "files_external:option",
                          str(m["mount_id"]), "readonly", "false" if ecriture else "true"], timeout=60)
        except ValueError:
            pass
    activity_log.record("disque", f"{nom} : {'écriture autorisée' if ecriture else 'lecture seule'}", request.state.nom)
    return {"ok": True, "ecriture": ecriture}


@router.post("/api/disques/{nom}/ejecter")
def eject_disk(nom: str, request: Request):
    chemin = files_source.disk_path(nom)
    if config.DEMO:
        files_source.EJECTED[nom] = time.time()
        return {"ok": True}
    code, _, err = commands.run_shell_on_host(f"sync && umount '{chemin}'", timeout=120)
    if code != 0 and "not mounted" not in err:
        raise HTTPException(409, "Le disque est en cours d'utilisation (lecture d'un film, copie…). Arrête ce qui l'utilise et réessaie.")
    files_source.EJECTED[nom] = time.time()
    activity_log.record("disque", f"{nom} éjecté", request.state.nom)
    return {"ok": True}


@router.post("/api/disques/{nom}/oublier")
def forget_disk(nom: str, request: Request):
    guard.require_admin(request, fort=True)
    if config.DEMO:
        return {"ok": True}
    try:
        x = auto_disks.forget(commands.run_on_host, commands.run_on_host_with_input, nom)
    except KeyError:
        raise HTTPException(404, "Seuls les disques ajoutés automatiquement peuvent être oubliés ici.")
    activity_log.record("disque", f"{x['nom']} oublié : il ne sera plus ajouté tout seul", request.state.nom)
    return {"ok": True}


@router.post("/api/disques/reprendre")
async def resume_disk(request: Request):
    guard.require_admin(request)
    uuid_ = str((await request.json()).get("uuid", ""))
    if not re.fullmatch(r"[\w-]{4,64}", uuid_):
        raise HTTPException(400, "Disque invalide")
    if not config.DEMO:
        auto_disks.resume(uuid_)
        threading.Thread(target=auto_disks.scan_disks, daemon=True).start()
    activity_log.record("disque", "Disque remis en ajout automatique", request.state.nom)
    return {"ok": True}
