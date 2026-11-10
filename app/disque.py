"""Écran Fichiers quand Nextcloud est désactivé : lecture directe des dossiers du serveur.

La racine est virtuelle : chaque entrée est un dossier du serveur, monté dans le
conteneur sous /srv. Rien n'est jamais lu ni écrit en dehors de ces dossiers.
"""
import hashlib
import json
import os
import subprocess
import shutil
import time
from datetime import datetime, timezone

UID, GID = 1000, 1000


def racines():
    """Nom affiché -> chemin sur le serveur. Réglable par DOSSIERS_DISQUE (« nom=chemin,… »)."""
    brut = os.environ.get("DOSSIERS_DISQUE",
                          "Films et séries=/srv/data/media,Téléchargements=/srv/data/downloads,Mes dossiers=/srv/data/dossiers")
    res = {}
    for morceau in brut.split(","):
        if "=" in morceau:
            nom, chemin = morceau.split("=", 1)
            res[nom.strip()] = chemin.strip()
    return res


class Refus(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def resoudre(chemin, disques, lecture_seule):
    """Chemin virtuel « /Films et séries/xx » -> (chemin réel, écriture autorisée)."""
    morceaux = [m for m in chemin.split("/") if m]
    if not morceaux:
        return None, False
    toutes = dict(racines())
    toutes.update(disques)
    if morceaux[0] not in toutes:
        raise Refus(404, "Dossier introuvable")
    base = os.path.realpath(toutes[morceaux[0]])
    reel = os.path.realpath(os.path.join(base, *morceaux[1:]))
    if reel != base and not reel.startswith(base + os.sep):
        raise Refus(400, "Chemin invalide")
    ecriture = morceaux[0] not in lecture_seule
    return reel, ecriture


def _element(chemin_virtuel, reel, ecriture, externe):
    st = os.stat(reel)
    dossier = os.path.isdir(reel)
    return {
        "nom": chemin_virtuel.rsplit("/", 1)[-1], "chemin": chemin_virtuel, "dossier": dossier,
        "taille": None if dossier else st.st_size,
        "modifie": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
        "type": None, "ecriture": ecriture, "externe": externe,
    }


def lister(chemin, disques, lecture_seule):
    if chemin == "/":
        elements = []
        for nom, reel in list(racines().items()) + list(disques.items()):
            if nom == "Mes dossiers":
                os.makedirs(reel, exist_ok=True)
                _proprietaire(reel)
            if os.path.isdir(reel):
                elements.append(_element("/" + nom, reel, nom not in lecture_seule, nom in disques))
        return {"chemin": "/", "elements": elements, "ecriture": False, "lien_nextcloud": None, "source": "disque"}
    reel, ecriture = resoudre(chemin, disques, lecture_seule)
    if not os.path.isdir(reel):
        raise Refus(404, "Dossier introuvable")
    elements = []
    for nom in sorted(os.listdir(reel), key=str.lower):
        if nom.startswith("."):
            continue
        try:
            elements.append(_element(chemin.rstrip("/") + "/" + nom, os.path.join(reel, nom), ecriture, False))
        except OSError:
            continue
    elements.sort(key=lambda e: (not e["dossier"], e["nom"].lower()))
    return {"chemin": chemin, "elements": elements, "ecriture": ecriture, "lien_nextcloud": None, "source": "disque"}


def _proprietaire(chemin):
    """Les fichiers créés par l'appli appartiennent à l'utilisateur du serveur, groupe du dossier parent."""
    try:
        gid = os.stat(os.path.dirname(chemin)).st_gid
        os.chown(chemin, UID, gid if gid else GID)
    except OSError:
        pass


def _ecrivable(chemin, disques, lecture_seule):
    reel, ecriture = resoudre(chemin, disques, lecture_seule)
    if reel is None or not ecriture or len([m for m in chemin.split("/") if m]) < 2:
        raise Refus(403, "Impossible ici : dossier en lecture seule.")
    return reel


def creer_dossier(chemin, disques, lecture_seule):
    reel = _ecrivable(chemin, disques, lecture_seule)
    if os.path.exists(reel):
        raise Refus(409, "Un élément porte déjà ce nom.")
    os.mkdir(reel)
    _proprietaire(reel)


def chemin_envoi(dossier, nom, disques, lecture_seule):
    reel = _ecrivable(dossier.rstrip("/") + "/" + nom, disques, lecture_seule)
    return reel


def fin_envoi(reel):
    _proprietaire(reel)


def chemin_lecture(chemin, disques, lecture_seule):
    reel, _ = resoudre(chemin, disques, lecture_seule)
    if reel is None or not os.path.isfile(reel):
        raise Refus(404, "Fichier introuvable")
    return reel


def deplacer(de, vers, disques, lecture_seule):
    src = _ecrivable(de, disques, lecture_seule)
    dst = _ecrivable(vers, disques, lecture_seule)
    if not os.path.exists(src):
        raise Refus(404, "Introuvable")
    if os.path.exists(dst):
        raise Refus(409, "Un élément porte déjà ce nom.")
    shutil.move(src, dst)


CORBEILLE = os.environ.get("JABBA_CORBEILLE", "/srv/data/.corbeille")
GARDE = 30 * 86400


def supprimer(chemin, disques, lecture_seule):
    """Pas de suppression définitive : l'élément part dans la corbeille du disque de données (30 jours)."""
    reel = _ecrivable(chemin, disques, lecture_seule)
    if not os.path.exists(reel):
        raise Refus(404, "Introuvable")
    ident = time.strftime("%Y%m%d-%H%M%S-") + os.urandom(3).hex()
    boite = os.path.join(CORBEILLE, ident)
    os.makedirs(boite, exist_ok=True)
    dossier = os.path.isdir(reel)
    taille = None if dossier else os.path.getsize(reel)
    shutil.move(reel, os.path.join(boite, os.path.basename(reel)))
    with open(os.path.join(boite, ".jabba.json"), "w") as f:
        json.dump({"chemin": chemin, "t": int(time.time()), "dossier": dossier, "taille": taille}, f, ensure_ascii=False)


def _boites():
    try:
        noms = sorted(os.listdir(CORBEILLE), reverse=True)
    except OSError:
        return
    for ident in noms:
        boite = os.path.join(CORBEILLE, ident)
        if not os.path.isdir(boite):
            continue
        try:
            with open(os.path.join(boite, ".jabba.json")) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            meta = {}
        contenu = [n for n in os.listdir(boite) if n != ".jabba.json"]
        if not contenu:
            continue
        meta.setdefault("t", int(os.stat(boite).st_mtime))
        yield ident, boite, contenu[0], meta


def lister_corbeille():
    res = []
    for ident, boite, nom, meta in _boites():
        reel = os.path.join(boite, nom)
        origine = meta.get("chemin")
        res.append({"id": ident, "nom": nom, "origine": (origine.rsplit("/", 1)[0] or "/") if origine else None, "supprime": meta["t"],
                    "dossier": os.path.isdir(reel), "taille": meta.get("taille"), "source": "disque"})
    return res


def _boite(ident):
    if not ident or "/" in ident or ident.startswith("."):
        raise Refus(400, "Élément invalide")
    for i, boite, nom, meta in _boites():
        if i == ident:
            return boite, nom, meta
    raise Refus(404, "Introuvable dans la corbeille")


def restaurer(ident, disques, lecture_seule):
    boite, nom, meta = _boite(ident)
    origine = meta.get("chemin")
    if not origine:
        raise Refus(409, "On ne sait plus d'où vient cet élément.")
    parent = origine.rsplit("/", 1)[0] or "/"
    dest_virtuel = origine
    reel = _ecrivable(dest_virtuel, disques, lecture_seule)
    if not os.path.isdir(os.path.dirname(reel)):
        raise Refus(409, f"Le dossier d'origine « {parent} » n'existe plus.")
    base, ext = os.path.splitext(nom)
    i = 2
    while os.path.exists(reel):
        dest_virtuel = f"{parent.rstrip('/')}/{base} ({i}){ext}"
        reel = _ecrivable(dest_virtuel, disques, lecture_seule)
        i += 1
    shutil.move(os.path.join(boite, nom), reel)
    shutil.rmtree(boite, ignore_errors=True)
    return dest_virtuel


def effacer(ident):
    boite, _, _ = _boite(ident)
    shutil.rmtree(boite)


def vider():
    n = 0
    for _, boite, _, _ in list(_boites()):
        shutil.rmtree(boite, ignore_errors=True)
        n += 1
    return n


def purger():
    """Efface ce qui est dans la corbeille depuis plus de 30 jours."""
    limite = time.time() - GARDE
    n = 0
    for _, boite, _, meta in list(_boites()):
        if meta["t"] < limite:
            shutil.rmtree(boite, ignore_errors=True)
            n += 1
    # Les aperçus non regardés depuis 60 jours sont refaits à la demande.
    limite_v = time.time() - 60 * 86400
    for dossier, _, fichiers in os.walk(VIGNETTES):
        for f in fichiers:
            try:
                if os.stat(os.path.join(dossier, f)).st_mtime < limite_v:
                    os.remove(os.path.join(dossier, f))
            except OSError:
                pass
    return n


# ------------------------------------------------------------ recherche
def rechercher(texte, disques, lecture_seule, noms=None, limite=60, delai=6.0):
    """Cherche par nom dans les dossiers du serveur. `noms` limite aux racines voulues."""
    texte = texte.lower()
    fin = time.time() + delai
    toutes = dict(racines())
    toutes.update(disques)
    res, complet = [], True
    for nom_racine, base in toutes.items():
        if noms is not None and nom_racine not in noms:
            continue
        base = os.path.realpath(base)
        for dossier, sous, fichiers in os.walk(base):
            sous[:] = sorted(d for d in sous if not d.startswith("."))
            rel = os.path.relpath(dossier, base)
            virtuel = "/" + nom_racine + ("" if rel == "." else "/" + rel.replace(os.sep, "/"))
            for n in sous + sorted(fichiers):
                if n.startswith(".") or texte not in n.lower():
                    continue
                reel = os.path.join(dossier, n)
                try:
                    res.append(_element(virtuel + "/" + n, reel, nom_racine not in lecture_seule, nom_racine in disques))
                except OSError:
                    continue
                if len(res) >= limite:
                    return res, False
            if time.time() > fin:
                return res, False
    return res, complet


# ------------------------------------------------------------ vignettes
VIGNETTES = os.environ.get("JABBA_VIGNETTES", "/data/vignettes")
IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}


def vignette(reel, taille):
    """Petite image JPEG pour la liste des fichiers (gardée en cache)."""
    if os.path.splitext(reel)[1].lower() not in IMAGES:
        raise Refus(404, "Pas d'aperçu")
    st = os.stat(reel)
    if st.st_size > 80 * 1024 * 1024:
        raise Refus(404, "Image trop lourde pour un aperçu")
    cle = hashlib.sha1(f"{reel}|{st.st_mtime_ns}|{st.st_size}|{taille}".encode()).hexdigest()
    cache = os.path.join(VIGNETTES, cle[:2], cle + ".jpg")
    if not os.path.exists(cache):
        import bac_a_sable
        # Pillow tourne à part, sans aucun droit : une image piégée ne peut rien faire.
        try:
            jpeg = bac_a_sable.vignette(reel, taille)
        except (RuntimeError, OSError, subprocess.SubprocessError):
            raise Refus(404, "Pas d'aperçu")
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache + ".tmp", "wb") as f:
            f.write(jpeg)
        os.replace(cache + ".tmp", cache)
    return cache


def est_dossier(chemin, disques, lecture_seule):
    reel, _ = resoudre(chemin, disques, lecture_seule)
    return bool(reel) and os.path.isdir(reel)
