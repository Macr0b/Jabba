"""Données fictives, utilisées seulement avec JABBA_DEMO=1 (tests hors de Jabba)."""
G = 1024 ** 3

ETAT = {
    "cpu": 4, "memoire": {"total": 8 * G, "utilise": int(2.0 * G)}, "temperature": 46,
    "systeme": {"total": 117 * G, "utilise": 9 * G}, "donnees": {"total": 916 * G, "utilise": 12 * G},
    "duree": 3 * 86400 + 4 * 3600, "redemarrage_requis": False,
    "outils_actifs": 5, "outils_total": 5, "soucis": [],
}

OUTILS = [
    {"id": "adresses", "nom": "Adresses", "role": "Les adresses https des outils", "etat": "actif", "lien": None, "dossier": "/opt/stacks/adresses", "conteneurs": [{"nom": "adresse-jellyfin", "etat": "running", "statut": "Up 1 hour"}], "modifiable": True, "systeme": True, "icone": False, "dans_appli": True},
    {"id": "dockge", "nom": "Dockge", "role": "Gestion avancée des outils", "etat": "actif", "lien": "https://jabba.exemple.ts.net:10000", "dossier": "/opt/dockge", "conteneurs": [{"nom": "dockge", "etat": "running", "statut": "Up 3 days"}], "modifiable": True, "systeme": False, "icone": False, "dans_appli": True},
    {"id": "filebrowser", "nom": "File Browser", "role": "Tous les dossiers du disque de données", "etat": "arrêté", "lien": "http://jabba.exemple.ts.net:8082", "dossier": "/opt/stacks/filebrowser", "conteneurs": [{"nom": "filebrowser", "etat": "running", "statut": "Up 3 days (healthy)"}], "modifiable": True, "systeme": False, "icone": False, "dans_appli": True},
    {"id": "jabba-app", "nom": "Appli Jabba", "role": "Cette appli", "etat": "actif", "lien": None, "dossier": "/opt/stacks/jabba-app", "conteneurs": [{"nom": "jabba-app", "etat": "running", "statut": "Up 2 minutes"}], "modifiable": False, "systeme": True, "icone": False, "dans_appli": True},
    {"id": "jellyfin", "nom": "Jellyfin", "role": "Films et séries", "etat": "actif", "lien": "https://jabba.exemple.ts.net:8443", "dossier": "/opt/stacks/jellyfin", "conteneurs": [{"nom": "jellyfin", "etat": "running", "statut": "Up 3 days (healthy)"}], "modifiable": True, "systeme": False, "icone": False, "dans_appli": True},
    {"id": "nextcloud", "nom": "Nextcloud", "role": "Fichiers, photos, agenda", "etat": "actif", "lien": "https://jabba.exemple.ts.net", "dossier": "/opt/stacks/nextcloud", "conteneurs": [{"nom": "nextcloud", "etat": "running", "statut": "Up 3 days"}, {"nom": "nextcloud-db", "etat": "running", "statut": "Up 3 days"}, {"nom": "nextcloud-redis", "etat": "running", "statut": "Up 3 days"}], "modifiable": True, "systeme": False, "icone": False, "dans_appli": False},
]

SYSTEME = {
    "nom": "jabba", "os": "Debian GNU/Linux 13 (trixie)", "noyau": "6.12.48-amd64", "duree": 3 * 86400 + 4 * 3600,
    "redemarrage_requis": False, "derniere_maj_auto": "2026-11-03 06:12",
    "appareils": [{"nom": "macbook", "os": "macOS", "en_ligne": True}, {"nom": "iphone", "os": "iOS", "en_ligne": True}],
    "version_appli": "0.5.1", "courant": {"secteur": True, "batterie": 100, "etat": "Full", "arret_prevu": False},
}

DISQUES = [
    {"nom": "sdb", "modele": "SSD 128 Go", "taille": 128 * 10 ** 9, "externe": False, "partitions": [
        {"nom": "sdb1", "taille": 512 * 1024 ** 2, "type": "vfat", "etiquette": None, "montage": "/boot/efi", "role": "Démarrage", "usage": None},
        {"nom": "sdb2", "taille": 120 * 10 ** 9, "type": "ext4", "etiquette": None, "montage": "/", "role": "Système", "usage": {"total": 117 * G, "utilise": 9 * G}}]},
    {"nom": "sda", "modele": "HDD 1 To", "taille": 10 ** 12, "externe": False, "partitions": [
        {"nom": "sda1", "taille": 10 ** 12, "type": "ext4", "etiquette": "data", "montage": "/srv/data", "role": "Données", "usage": {"total": 916 * G, "utilise": 12 * G}}]},
    {"nom": "sdc", "modele": "R2-D2", "taille": 2 * 10 ** 12, "externe": True, "partitions": [
        {"nom": "sdc1", "taille": 2 * 10 ** 12, "type": "exfat", "etiquette": "R2-D2", "montage": "/srv/externe", "role": "Disque externe", "usage": {"total": 1863 * G, "utilise": 1210 * G}, "disque_jabba": {"nom": "R2-D2", "ecriture": False, "auto": False}}]},
    {"nom": "sdd", "modele": "Clé USB", "taille": 64 * 10 ** 9, "externe": True, "partitions": [
        {"nom": "sdd1", "taille": 64 * 10 ** 9, "type": "exfat", "etiquette": "PHOTOS 2019", "montage": "/srv/disques/PHOTOS-2019", "role": "Disque externe", "usage": {"total": 59 * G, "utilise": 22 * G}, "disque_jabba": {"nom": "PHOTOS 2019", "ecriture": False, "auto": True}}]},
    {"nom": "sde", "modele": "Disque Mac", "taille": 500 * 10 ** 9, "externe": True, "partitions": [
        {"nom": "sde2", "taille": 500 * 10 ** 9, "type": "apfs", "etiquette": "Time Machine", "montage": None, "role": "Non utilisé", "usage": None, "disque_jabba": None, "oublie": None, "illisible": "APFS (format Mac récent)"}]},
]

ARBO = {
    "/": [("Documents", True, None, True), ("Photos", True, None, True), ("Films et séries", True, None, False), ("R2-D2", True, None, False), ("Téléchargements", True, None, False), ("Lisez-moi.md", False, 1200, True)],
    "/Documents": [("Atelier", True, None, True), ("Administratif", True, None, True)],
    "/Documents/Atelier": [("Plans CNC", True, None, True), ("Photos chantier", True, None, True), ("Devis vinaigrier.pdf", False, 220_000, True), ("Gabarit tiroir.ai", False, 4_800_000, True), ("Liste matériaux.xlsx", False, 36_000, True)],
    "/R2-D2": [("Flim", True, None, False), ("Séries", True, None, False)],
}


def fichiers(chemin):
    elements = []
    for nom, dossier, taille, ecriture in ARBO.get(chemin, []):
        elements.append({"nom": nom, "chemin": (chemin.rstrip("/") + "/" + nom), "dossier": dossier,
                         "taille": taille if taille else (340_000_000 if dossier else None),
                         "modifie": "2026-11-02T10:12:00+00:00", "type": None, "ecriture": ecriture,
                         "externe": not ecriture})
    lecture_seule = any(chemin.startswith(p) for p in ("/Films", "/R2-D2", "/Téléchargements"))
    return {"chemin": chemin, "elements": elements, "ecriture": not lecture_seule, "lien_nextcloud": "https://jabba.exemple.ts.net/apps/files/?dir=" + chemin}


def recherche(q):
    res = []
    for chemin, liste in ARBO.items():
        for nom, dossier, taille, ecriture in liste:
            if q.lower() in nom.lower():
                res.append({"nom": nom, "chemin": chemin.rstrip("/") + "/" + nom, "dossier": dossier, "taille": taille,
                            "modifie": "2026-11-02T10:12:00+00:00", "type": None, "ecriture": ecriture, "externe": False})
    return {"elements": res, "complet": True}


CORBEILLE = [
    {"id": "a.d1", "nom": "Ancien devis.pdf", "origine": "/Documents/Atelier", "supprime": 1793600000, "dossier": False, "taille": 180_000, "source": "nextcloud"},
    {"id": "b.d2", "nom": "Brouillons", "origine": "/Documents", "supprime": 1793400000, "dossier": True, "taille": 3_400_000, "source": "nextcloud"},
    {"id": "20261101-101010-abc123", "nom": "film-test.mkv", "origine": "/Téléchargements/film-test.mkv", "supprime": 1793300000, "dossier": False, "taille": 1_400_000_000, "source": "disque"},
]

JOURNAL = [
    {"t": 1793770000, "famille": "disque", "texte": "Nouveau disque : PHOTOS 2019 — Prêt, en lecture seule, visible dans Nextcloud. Jellyfin le voit dans /disques/PHOTOS-2019."},
    {"t": 1793760000, "famille": "fichier", "texte": "3 fichiers envoyés dans /Documents/Atelier", "qui": "Alex"},
    {"t": 1793750000, "famille": "outil", "texte": "Mise à jour de Jellyfin : terminé", "qui": "Alex"},
    {"t": 1793740000, "famille": "admin", "texte": "Mot de passe du serveur saisi", "qui": "Alex"},
    {"t": 1793700000, "famille": "courant", "texte": "Courant revenu — Le serveur est de nouveau sur secteur (82 %)."},
    {"t": 1793699000, "famille": "courant", "texte": "Coupure de courant — Le serveur tourne sur batterie (97 %). Il s'éteindra proprement sous 15 %."},
    {"t": 1793600000, "famille": "appli", "texte": "Appli Jabba 0.5.0 installée (avant : 0.4.0)"},
]
