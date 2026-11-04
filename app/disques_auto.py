"""Ajout automatique des disques USB : on branche, c'est prêt.

Un disque USB jamais vu est déclaré sur le serveur en lecture seule
(/srv/disques/<nom>, ouverture automatique), puis ajouté à Nextcloud s'il est
activé. Jellyfin le voit dans /disques ; les médiathèques restent à créer à la main.
Les disques « oubliés » ne sont plus jamais ajoutés tout seuls.
"""
import json
import os
import re
import threading
import time

DOSSIER = os.environ.get("JABBA_DATA", "/data")
FICHIER = os.path.join(DOSSIER, "disques.json")
RACINE = "/srv/disques"
_VERROU = threading.Lock()

# Formats lisibles sans rien installer, et options de montage.
FORMATS = {
    "exfat": ("exfat", "uid=1000,gid=1000"),
    "vfat": ("vfat", "uid=1000,gid=1000,utf8"),
    "ntfs": ("ntfs3", "uid=1000,gid=1000"),
    "ext4": ("ext4", ""),
}
# Partitions techniques à ignorer (démarrage, récupération Windows) et taille minimale.
TYPES_TECHNIQUES = {"c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "de94bba4-06d1-4d40-a16a-bfd50179d6ac", "0xef", "0x27"}
TAILLE_MIN = 300 * 1024 * 1024
ILLISIBLES = {"apfs": "APFS (format Mac récent)", "hfsplus": "Mac OS étendu (HFS+)", "btrfs": "Btrfs", "xfs": "XFS"}


def lire():
    try:
        with open(FICHIER) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("disques", [])   # {nom, chemin, uuid, format, ajoute}
    d.setdefault("oublies", [])   # uuid
    d.setdefault("signales", [])  # uuid illisibles déjà signalés
    return d


def ecrire(d):
    os.makedirs(DOSSIER, exist_ok=True)
    with open(FICHIER + ".tmp", "w") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(FICHIER + ".tmp", FICHIER)


def connus():
    """nom -> chemin pour les disques ajoutés automatiquement."""
    return {x["nom"]: x["chemin"] for x in lire()["disques"]}


def uuids_fstab(fstab):
    res = set()
    for ligne in fstab.splitlines():
        champs = ligne.split()
        if champs and not ligne.lstrip().startswith("#") and champs[0].upper().startswith("UUID="):
            res.add(champs[0].split("=", 1)[1].strip('"').lower())
    return res


def nom_propre(etiquette, uuid, pris):
    base = (etiquette or "").strip() or f"Disque {uuid[:4].upper()}"
    base = re.sub(r"[^\w .-]", "", base)[:40].strip() or f"Disque {uuid[:4].upper()}"
    nom, i = base, 2
    while nom.lower() in pris:
        nom, i = f"{base} {i}", i + 1
    dossier = re.sub(r"[^\w.-]", "-", nom).strip("-") or uuid[:8]
    return nom, dossier


def candidats(hote):
    """Partitions USB lisibles, avec un identifiant, absentes de /etc/fstab."""
    code, sortie, _ = hote(["lsblk", "-J", "-b", "-o", "NAME,PATH,FSTYPE,LABEL,UUID,TRAN,RM,TYPE,MOUNTPOINT,SIZE,HOTPLUG,PARTTYPE"])
    if code != 0:
        return [], []
    _, fstab, _ = hote(["cat", "/etc/fstab"])
    deja = uuids_fstab(fstab)
    bons, mauvais = [], []
    for d in json.loads(sortie).get("blockdevices", []):
        if d.get("type") != "disk":
            continue
        usb = d.get("tran") == "usb" or str(d.get("rm")) in ("1", "True", "true") or str(d.get("hotplug")) in ("1", "True", "true")
        if not usb:
            continue
        for p in (d.get("children") or [d]):
            fs, uuid = (p.get("fstype") or "").lower(), (p.get("uuid") or "").lower()
            if not uuid or uuid in deja or p.get("mountpoint"):
                continue
            if (p.get("parttype") or "").lower() in TYPES_TECHNIQUES or (p.get("label") or "").upper() in ("EFI", "RECOVERY"):
                continue
            try:
                if int(p.get("size") or 0) < TAILLE_MIN:
                    continue
            except (TypeError, ValueError):
                pass
            if fs in FORMATS:
                bons.append({"uuid": uuid, "format": fs, "etiquette": p.get("label"), "chemin_dev": p.get("path")})
            elif fs in ILLISIBLES:
                mauvais.append({"uuid": uuid, "format": fs, "etiquette": p.get("label")})
    return bons, mauvais


def ajouter(hote, hote_entree, part, pris):
    """Déclare le disque sur le serveur. Renvoie l'entrée créée."""
    nom, dossier = nom_propre(part["etiquette"], part["uuid"], pris)
    chemin = f"{RACINE}/{dossier}"
    type_, options = FORMATS[part["format"]]
    opts = ",".join(o for o in ["ro", options, "nofail", "x-systemd.automount", "x-systemd.device-timeout=5s"] if o)
    uuid = part["uuid"].upper() if part["format"] in ("exfat", "vfat", "ntfs") else part["uuid"]
    ligne = f"UUID={uuid} {chemin} {type_} {opts} 0 0"
    _, fstab, _ = hote(["cat", "/etc/fstab"])
    nouveau = fstab.rstrip("\n") + f"\n# Ajouté par l'appli Jabba : {nom}\n{ligne}\n"
    code, _, err = hote_entree(["sh", "-c", f"mkdir -p '{chemin}' && cp /etc/fstab /etc/fstab.jabba-bak && cat > /etc/fstab"], nouveau)
    if code != 0:
        raise RuntimeError(err[:200])
    _, unite, _ = hote(["systemd-escape", "--path", "--suffix=automount", chemin])
    hote(["sh", "-c", f"systemctl daemon-reload && systemctl start '{unite.strip()}' && ls '{chemin}' >/dev/null 2>&1"], timeout=30)
    entree = {"nom": nom, "chemin": chemin, "dossier": dossier, "uuid": part["uuid"], "format": part["format"],
              "ajoute": int(time.time()), "nextcloud": None}
    with _VERROU:
        d = lire()
        d["disques"].append(entree)
        ecrire(d)
    return entree


def relier_nextcloud(hote, entree):
    """Ajoute le disque à Nextcloud (lecture seule). Sans effet si Nextcloud est arrêté."""
    occ = ["docker", "exec", "-u", "www-data", "nextcloud", "php", "occ"]
    code, sortie, _ = hote(occ + ["files_external:create", entree["nom"], "local", "null::null",
                                  "-c", f"datadir=/mnt/disques/{entree['dossier']}"], timeout=60)
    m = re.search(r"(\d+)\s*$", sortie.strip()) if code == 0 else None
    if not m:
        return False
    hote(occ + ["files_external:option", m.group(1), "readonly", "true"], timeout=60)
    with _VERROU:
        d = lire()
        for x in d["disques"]:
            if x["uuid"] == entree["uuid"]:
                x["nextcloud"] = int(m.group(1))
        ecrire(d)
    return True


def oublier(hote, hote_entree, nom):
    """Retire le disque du serveur et de Nextcloud. Il ne sera plus ajouté tout seul."""
    with _VERROU:
        d = lire()
        x = next((x for x in d["disques"] if x["nom"] == nom), None)
    if not x:
        raise KeyError(nom)
    chemin = x["chemin"]
    _, unite, _ = hote(["systemd-escape", "--path", "--suffix=automount", chemin])
    hote(["sh", "-c", f"systemctl stop '{unite.strip()}'; umount '{chemin}' 2>/dev/null; true"], timeout=60)
    _, fstab, _ = hote(["cat", "/etc/fstab"])
    lignes = []
    for ligne in fstab.splitlines():
        champs = ligne.split()
        if len(champs) >= 2 and not ligne.lstrip().startswith("#") and champs[1] == chemin:
            continue
        if ligne.strip() == f"# Ajouté par l'appli Jabba : {nom}":
            continue
        lignes.append(ligne)
    hote_entree(["sh", "-c", "cp /etc/fstab /etc/fstab.jabba-bak && cat > /etc/fstab && systemctl daemon-reload"], "\n".join(lignes) + "\n")
    hote(["rmdir", chemin])
    if x.get("nextcloud"):
        hote(["docker", "exec", "-u", "www-data", "nextcloud", "php", "occ", "files_external:delete", "-y", str(x["nextcloud"])], timeout=60)
    with _VERROU:
        d = lire()
        d["disques"] = [y for y in d["disques"] if y["nom"] != nom]
        if x["uuid"] not in d["oublies"]:
            d["oublies"].append(x["uuid"])
        ecrire(d)
    return x


def reprendre(uuid):
    """Un disque oublié redevient un disque à ajouter automatiquement."""
    with _VERROU:
        d = lire()
        d["oublies"] = [u for u in d["oublies"] if u != uuid.lower()]
        ecrire(d)


def present(hote, uuid):
    """Le disque est-il branché ? (sans réveiller l'ouverture automatique)"""
    if not uuid:
        return True
    code, sortie, _ = hote(["blkid", "-U", uuid], timeout=10)
    if code == 0:
        return True
    code, _, _ = hote(["blkid", "-U", uuid.upper()], timeout=10)
    return code == 0


def scruter(hote, hote_entree, nextcloud_actif, prevenir, autres_noms):
    """Un passage : ajoute les nouveaux disques. Appelé toutes les 15 secondes."""
    bons, mauvais = candidats(hote)
    d = lire()
    for m in mauvais:
        if m["uuid"] not in d["signales"]:
            d["signales"].append(m["uuid"])
            ecrire(d)
            prevenir("nouveau_disque", "Disque illisible",
                     f"« {m['etiquette'] or 'Sans nom'} » est en {ILLISIBLES[m['format']]}. Le serveur ne sait pas le lire : "
                     "reformate-le en exFAT depuis le Mac (Utilitaire de disque) pour qu'il soit lisible partout.", "/#/disques")
    for b in bons:
        if b["uuid"] in d["oublies"]:
            continue
        pris = {n.lower() for n in autres_noms()} | {x["nom"].lower() for x in lire()["disques"]}
        try:
            entree = ajouter(hote, hote_entree, b, pris)
        except RuntimeError as e:
            prevenir("nouveau_disque", "Disque non ajouté", f"« {b['etiquette'] or 'Sans nom'} » : {e}", "/#/disques")
            continue
        nc = nextcloud_actif() and relier_nextcloud(hote, entree)
        prevenir("nouveau_disque", f"Nouveau disque : {entree['nom']}",
                 "Prêt, en lecture seule" + (", visible dans Nextcloud" if nc else "") +
                 f". Jellyfin le voit dans /disques/{entree['dossier']}.", "/#/disques")
