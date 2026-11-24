"""Données fictives, utilisées seulement avec JABBA_DEMO=1 (tests hors de Jabba).

Sommaire :
  folder_listing : le contenu d'un dossier de démonstration
  search_results : la recherche dans les dossiers de démonstration
"""

G = 1024**3

HOME_STATE = {
    "cpu": 4,
    "memory": {"total": 8 * G, "used": int(2.0 * G)},
    "temperature": 46,
    "system": {"total": 117 * G, "used": 9 * G},
    "data": {"total": 916 * G, "used": 12 * G},
    "duration": 3 * 86400 + 4 * 3600,
    "redemarrage_requis": False,
    "active_tools": 5,
    "tools_total": 5,
    "issues": [],
}

TOOLS = [
    {
        "id": "adguard-home",
        "name": "AdGuard Home",
        "role": "Bloque les publicités et les traqueurs, appareil par appareil",
        "state": "actif",
        "link": "https://jabba.exemple.ts.net:3000",
        "folder": "/opt/stacks/adguard-home",
        "containers": [{"name": "adguard-home", "state": "running", "status": "Up 1 hour"}],
        "editable": True,
        "system": False,
        "icon": False,
        "in_app": False,
        "port_https": 3000,
        "optional": True,
    },
    {
        "id": "adresses",
        "name": "Adresses",
        "role": "Les adresses https des outils",
        "state": "actif",
        "link": None,
        "folder": "/opt/stacks/adresses",
        "containers": [{"name": "adresse-jellyfin", "state": "running", "status": "Up 1 hour"}],
        "editable": True,
        "system": True,
        "icon": False,
        "in_app": True,
    },
    {
        "id": "dockge",
        "name": "Dockge",
        "role": "Gestion avancée des outils",
        "state": "actif",
        "link": "https://jabba.exemple.ts.net:10000",
        "folder": "/opt/dockge",
        "containers": [{"name": "dockge", "state": "running", "status": "Up 3 days"}],
        "editable": True,
        "system": False,
        "icon": False,
        "in_app": True,
    },
    {
        "id": "jabba-app",
        "name": "Appli Jabba",
        "role": "Cette appli",
        "state": "actif",
        "link": None,
        "folder": "/opt/stacks/jabba-app",
        "containers": [{"name": "jabba-app", "state": "running", "status": "Up 2 minutes"}],
        "editable": False,
        "system": True,
        "icon": False,
        "in_app": True,
    },
    {
        "id": "jellyfin",
        "name": "Jellyfin",
        "role": "Films et séries",
        "state": "actif",
        "link": "https://jabba.exemple.ts.net:8443",
        "folder": "/opt/stacks/jellyfin",
        "containers": [{"name": "jellyfin", "state": "running", "status": "Up 3 days (healthy)"}],
        "editable": True,
        "system": False,
        "icon": False,
        "in_app": True,
    },
    {
        "id": "nextcloud",
        "name": "Nextcloud",
        "role": "Fichiers, photos, agenda",
        "state": "actif",
        "link": "https://jabba.exemple.ts.net",
        "folder": "/opt/stacks/nextcloud",
        "containers": [
            {"name": "nextcloud", "state": "running", "status": "Up 3 days"},
            {"name": "nextcloud-db", "state": "running", "status": "Up 3 days"},
            {"name": "nextcloud-redis", "state": "running", "status": "Up 3 days"},
        ],
        "editable": True,
        "system": False,
        "icon": False,
        "in_app": False,
    },
]

SYSTEM = {
    "name": "jabba",
    "os": "Debian GNU/Linux 13 (trixie)",
    "noyau": "6.12.48-amd64",
    "duration": 3 * 86400 + 4 * 3600,
    "redemarrage_requis": False,
    "last_auto_update": "2026-11-03 06:12",
    "devices": [
        {"name": "macbook", "os": "macOS", "online": True},
        {"name": "iphone", "os": "iOS", "online": True},
    ],
    "app_version": "0.6.0",
    "power": {"mains": True, "battery": 100, "state": "Full", "shutdown_planned": False},
}

DISKS = [
    {
        "name": "sdb",
        "title": "Disque système",
        "genre": "Disque interne",
        "model": "SSD 128 Go",
        "size": 128 * 10**9,
        "external": False,
        "partitions": [
            {
                "name": "sdb1",
                "size": 512 * 1024**2,
                "type": "vfat",
                "label": None,
                "mount": "/boot/efi",
                "role": "Démarrage",
                "usage": None,
                "technical": True,
            },
            {
                "name": "sdb2",
                "size": 120 * 10**9,
                "type": "ext4",
                "label": None,
                "mount": "/",
                "role": "Système",
                "usage": {"total": 117 * G, "used": 9 * G},
            },
        ],
    },
    {
        "name": "sda",
        "title": "Disque de données",
        "genre": "Disque interne",
        "model": "HDD 1 To",
        "size": 10**12,
        "external": False,
        "partitions": [
            {
                "name": "sda1",
                "size": 10**12,
                "type": "ext4",
                "label": "data",
                "mount": "/srv/data",
                "role": "Données",
                "usage": {"total": 916 * G, "used": 12 * G},
            }
        ],
    },
    {
        "name": "sdc",
        "title": "R2-D2",
        "genre": "Disque externe USB",
        "model": "WDC WD20SDRW",
        "size": 2 * 10**12,
        "external": True,
        "partitions": [
            {
                "name": "sdc1",
                "size": 2 * 10**12,
                "type": "exfat",
                "label": "R2-D2",
                "mount": "/srv/disques/R2-D2",
                "role": "Disque externe",
                "usage": {"total": 1863 * G, "used": 1210 * G},
                "jabba_disk": {"name": "R2-D2", "writable": False, "auto": False},
            }
        ],
    },
    {
        "name": "sdd",
        "title": "PHOTOS 2019",
        "genre": "Disque externe USB",
        "model": "Clé USB",
        "size": 64 * 10**9,
        "external": True,
        "partitions": [
            {
                "name": "sdd1",
                "size": 64 * 10**9,
                "type": "exfat",
                "label": "PHOTOS 2019",
                "mount": "/srv/disques/PHOTOS-2019",
                "role": "Disque externe",
                "usage": {"total": 59 * G, "used": 22 * G},
                "jabba_disk": {"name": "PHOTOS 2019", "writable": False, "auto": True},
            }
        ],
    },
    {
        "name": "sde",
        "title": "Time Machine",
        "genre": "Disque externe USB",
        "model": "Disque Mac",
        "size": 500 * 10**9,
        "external": True,
        "partitions": [
            {
                "name": "sde2",
                "size": 500 * 10**9,
                "type": "apfs",
                "label": "Time Machine",
                "mount": None,
                "role": "Non utilisé",
                "usage": None,
                "jabba_disk": None,
                "forgotten": None,
                "unreadable": "APFS (format Mac récent)",
            }
        ],
    },
]

TREE = {
    "/": [
        ("Documents", True, None, True),
        ("Photos", True, None, True),
        ("Films et séries", True, None, False),
        ("R2-D2", True, None, False),
        ("Téléchargements", True, None, False),
        ("Lisez-moi.md", False, 1200, True),
    ],
    "/Documents": [("Atelier", True, None, True), ("Administratif", True, None, True)],
    "/Documents/Atelier": [
        ("Plans CNC", True, None, True),
        ("Photos chantier", True, None, True),
        ("Devis vinaigrier.pdf", False, 220_000, True),
        ("Gabarit tiroir.ai", False, 4_800_000, True),
        ("Liste matériaux.xlsx", False, 36_000, True),
    ],
    "/R2-D2": [("Flim", True, None, False), ("Séries", True, None, False)],
}


def folder_listing(path):
    entries = []
    for name, folder, size, writable in TREE.get(path, []):
        entries.append(
            {
                "name": name,
                "path": (path.rstrip("/") + "/" + name),
                "folder": folder,
                "size": size if size else (340_000_000 if folder else None),
                "modified": "2026-11-02T10:12:00+00:00",
                "type": None,
                "writable": writable,
                "external": not writable,
            }
        )
    read_only = any(path.startswith(prefix) for prefix in ("/Films", "/R2-D2", "/Téléchargements"))
    return {
        "path": path,
        "items": entries,
        "writable": not read_only,
        "nextcloud_link": "https://jabba.exemple.ts.net/apps/files/?dir=" + path,
    }


def search_results(query):
    result = []
    for path, items in TREE.items():
        for name, folder, size, writable in items:
            if query.lower() in name.lower():
                result.append(
                    {
                        "name": name,
                        "path": path.rstrip("/") + "/" + name,
                        "folder": folder,
                        "size": size,
                        "modified": "2026-11-02T10:12:00+00:00",
                        "type": None,
                        "writable": writable,
                        "external": False,
                    }
                )
    return {"items": result, "full": True}


TRASH = [
    {
        "id": "a.d1",
        "name": "Ancien devis.pdf",
        "origin": "/Documents/Atelier",
        "deleted": 1793600000,
        "folder": False,
        "size": 180_000,
        "source": "nextcloud",
    },
    {
        "id": "b.d2",
        "name": "Brouillons",
        "origin": "/Documents",
        "deleted": 1793400000,
        "folder": True,
        "size": 3_400_000,
        "source": "nextcloud",
    },
    {
        "id": "20261101-101010-abc123",
        "name": "film-test.mkv",
        "origin": "/Téléchargements/film-test.mkv",
        "deleted": 1793300000,
        "folder": False,
        "size": 1_400_000_000,
        "source": "disque",
    },
]

ACTIVITY = [
    {
        "t": 1793770000,
        "family": "disque",
        "text": "Nouveau disque : PHOTOS 2019 — Prêt, en lecture seule, visible dans Nextcloud. Jellyfin le voit dans /disques/PHOTOS-2019.",
    },
    {"t": 1793760000, "family": "fichier", "text": "3 fichiers envoyés dans /Documents/Atelier", "who": "Alex"},
    {"t": 1793750000, "family": "outil", "text": "Mise à jour de Jellyfin : terminé", "who": "Alex"},
    {"t": 1793740000, "family": "admin", "text": "Mot de passe du serveur saisi", "who": "Alex"},
    {"t": 1793700000, "family": "courant", "text": "Courant revenu — Le serveur est de nouveau sur secteur (82 %)."},
    {
        "t": 1793699000,
        "family": "courant",
        "text": "Coupure de courant — Le serveur tourne sur batterie (97 %). Il s'éteindra proprement sous 15 %.",
    },
    {"t": 1793600000, "family": "appli", "text": "Appli Jabba 0.5.0 installée (avant : 0.4.0)"},
]
