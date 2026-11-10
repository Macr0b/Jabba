"""Sauvegardes du serveur, avec restic.

Chaque nuit (ou à la demande), une copie chiffrée de ce qui n'existe nulle part
ailleurs part sur un disque choisi : les fichiers (/srv/data, sans les films et
les téléchargements), les réglages des outils (/opt/stacks, avec une copie
propre des bases de données) et ceux du serveur (/etc).

- restic ne garde qu'une fois ce qui ne change pas : chaque sauvegarde ne prend
  que la place des nouveautés ;
- l'historique garde 7 jours, 4 semaines et 6 mois ;
- tout est chiffré avec une phrase de passe, montrée une seule fois à l'admin ;
- la copie est rangée dans un dossier caché du disque, `.sauvegardes-jabba` ;
  un disque en lecture seule n'est ouvert en écriture que le temps de la copie ;
- on ne remplace jamais rien en restaurant : la copie revient dans
  /srv/data/dossiers/Restaurations/<date>/, à côté.

La copie tourne sur le serveur lui-même (systemd-run) : elle va au bout même si
l'appli redémarre. Elle écrit son résultat dans donnees/sauvegarde-etat.json.
"""
import json
import os
import secrets
import shlex
import subprocess
import threading
import time
from datetime import datetime

DOSSIER = os.environ.get("JABBA_DATA", "/data")
FICHIER = os.path.join(DOSSIER, "sauvegardes.json")
CLE = os.path.join(DOSSIER, "sauvegardes.cle")
ETAT = os.path.join(DOSSIER, "sauvegarde-etat.json")
SOUS_DOSSIER = ".sauvegardes-jabba"
GARDER = {"jours": 7, "semaines": 4, "mois": 6}
SOURCES = {
    "fichiers": ("Fichiers", "Tes dossiers sur le serveur (/srv/data), sans les films ni les téléchargements"),
    "outils": ("Réglages des outils", "Nextcloud (avec sa base), Jellyfin, Jabba… (/opt/stacks)"),
    "systeme": ("Réglages du serveur", "Réseau, comptes, disques… (/etc)"),
}
CHEMINS = {"fichiers": "/srv/data", "outils": "/opt/stacks", "systeme": "/etc"}
EXCLURE = ["/srv/data/downloads", "/srv/data/media", "/srv/data/dossiers/Restaurations", "/srv/data/.corbeille", "**/transcodes", "**/cache",
           "**/.cache", "**/Cache", "/opt/stacks/*/donnees/sauvegarde-etat.json"]
_verrou = threading.RLock()


# ---------------------------------------------------------------- réglages
def lire():
    try:
        with open(FICHIER) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("actif", False)
    d.setdefault("disque", None)       # nom du disque choisi
    d.setdefault("chemin", None)       # point de montage du disque
    d.setdefault("heure", "03:30")
    d.setdefault("sources", {k: True for k in SOURCES})
    d.setdefault("unite", None)        # tâche en cours sur le serveur
    d.setdefault("lancee", 0)
    d.setdefault("derniere_reussie", None)
    d.setdefault("alerte_absent", None)
    return d


def ecrire(d):
    with _verrou:
        os.makedirs(DOSSIER, exist_ok=True)
        tmp = FICHIER + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, FICHIER)


def nouvelle_phrase():
    """Six groupes de quatre caractères faciles à recopier (sans 0/O ni 1/I/L)."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(6))


def phrase():
    try:
        with open(CLE) as f:
            return f.read().strip()
    except OSError:
        return None


def poser_phrase(p):
    with open(CLE, "w") as f:
        f.write(p + "\n")
    os.chmod(CLE, 0o600)


def depot(d):
    return os.path.join(d["chemin"], SOUS_DOSSIER)


# ---------------------------------------------------------------- la copie
def script(d, donnees_hote, demo=False):
    """Le script shell qui fait une sauvegarde complète, sur le serveur."""
    sources = [CHEMINS[k] for k, v in d["sources"].items() if v and k in CHEMINS]
    if demo:
        sources = [os.environ.get("JABBA_DEMO_SAUVER", "/tmp")]
    exclure = " ".join(f"--exclude {shlex.quote(x)}" for x in EXCLURE)
    q = shlex.quote
    return f"""set -uo pipefail
ETAT={q(os.path.join(donnees_hote, "sauvegarde-etat.json"))}
DISQUE={q(d["chemin"])}
export RESTIC_REPOSITORY={q(depot(d))}
export RESTIC_PASSWORD_FILE={q(os.path.join(donnees_hote, "sauvegardes.cle"))}
export RESTIC_CACHE_DIR={q("/tmp/restic-jabba-cache" if demo else "/var/cache/restic-jabba")}
DEBUT=$(date +%s); ETAPE=disque; REMONTER=0; AVERT=""; RESUME=null
TMP=$(mktemp)
fin() {{
  code=$?
  [ "$REMONTER" = 1 ] && mount -o remount,ro "$DISQUE" 2>/dev/null
  rm -f "$TMP"
  printf '{{"debut":%s,"fin":%s,"code":%s,"etape":"%s","avert":"%s","resume":%s}}\\n' "$DEBUT" "$(date +%s)" "$code" "$ETAPE" "$AVERT" "$RESUME" > "$ETAT"
}}
trap fin EXIT
ls "$DISQUE" >/dev/null 2>&1
{"" if demo else 'mountpoint -q "$DISQUE" || exit 10'}
{"" if demo else 'case ",$(findmnt -no OPTIONS "$DISQUE")," in *,ro,*) mount -o remount,rw "$DISQUE" || exit 11; REMONTER=1;; esac'}
mkdir -p "$RESTIC_REPOSITORY" || exit 12
ETAPE=base
{"" if demo else _script_bases()}
ETAPE=init
restic cat config >/dev/null 2>&1 || restic init >/dev/null || exit 13
restic unlock >/dev/null 2>&1
ETAPE=copie
restic backup --tag jabba --exclude-caches {exclure} {" ".join(q(s) for s in sources)} --json > "$TMP"
c=$?
RESUME=$(grep '"message_type":"summary"' "$TMP" | tail -1)
[ -n "$RESUME" ] || RESUME=null
if [ $c = 3 ]; then AVERT="${{AVERT}}Quelques fichiers illisibles n'ont pas été copiés. "; elif [ $c != 0 ]; then exit 14; fi
ETAPE=menage
restic forget --tag jabba --keep-daily {GARDER["jours"]} --keep-weekly {GARDER["semaines"]} --keep-monthly {GARDER["mois"]} --prune >/dev/null || AVERT="${{AVERT}}Le tri des anciennes copies a échoué. "
ETAPE=fini
exit 0
"""


def _script_bases():
    """Copie propre des bases de données (conteneurs *-db) avant la sauvegarde, dans le dossier de leur outil."""
    return r"""for c in $(docker ps --format '{{.Names}}' | grep -- '-db$'); do
  dir=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$c")
  [ -d "$dir" ] || continue
  img=$(docker inspect -f '{{.Config.Image}}' "$c")
  case "$img" in
    *postgres*) docker exec "$c" sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}"' > "$dir/sauvegarde-base.sql.tmp" ;;
    *mariadb*|*mysql*) docker exec "$c" sh -c 'p="${MARIADB_ROOT_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}"; (command -v mariadb-dump >/dev/null && exec mariadb-dump --all-databases --single-transaction -uroot -p"$p") || exec mysqldump --all-databases --single-transaction -uroot -p"$p"' > "$dir/sauvegarde-base.sql.tmp" ;;
    *) continue ;;
  esac
  if [ -s "$dir/sauvegarde-base.sql.tmp" ]; then mv "$dir/sauvegarde-base.sql.tmp" "$dir/sauvegarde-base.sql"; chmod 600 "$dir/sauvegarde-base.sql"
  else rm -f "$dir/sauvegarde-base.sql.tmp"; AVERT="${AVERT}Base de $c non copiée. "; fi
done"""


def lire_etat():
    try:
        with open(ETAT) as f:
            return json.loads(f.read() or "{}")
    except (OSError, ValueError):
        return {}


ETAPES = {"disque": "Le disque des sauvegardes est introuvable ou ne s'ouvre pas.",
          "base": "La copie des bases de données a échoué.",
          "init": "Le dossier des sauvegardes ne s'ouvre pas (phrase de passe changée ?).",
          "copie": "La copie des fichiers a échoué.",
          "menage": "Le tri des anciennes copies a échoué."}


def resultat(e):
    """Le dernier résultat, en clair."""
    if not e:
        return None
    r = e.get("resume") or {}
    ok = e.get("code") == 0
    return {
        "ok": ok,
        "debut": e.get("debut"), "fin": e.get("fin"),
        "duree": max(0, (e.get("fin") or 0) - (e.get("debut") or 0)),
        "erreur": None if ok else ETAPES.get(e.get("etape"), "La sauvegarde s'est arrêtée."),
        "avert": (e.get("avert") or "").strip() or None,
        "ajoute": r.get("data_added"),
        "fichiers": r.get("total_files_processed"),
        "taille": r.get("total_bytes_processed"),
        "nouveaux": r.get("files_new"), "modifies": r.get("files_changed"),
        "instantane": r.get("snapshot_id"),
    }


def due(d, maintenant=None):
    """Faut-il lancer la sauvegarde de cette nuit ? (manquée : dès que possible)"""
    if not d["actif"] or not d["chemin"]:
        return False
    maintenant = maintenant or datetime.now()
    h, m = (int(x) for x in d["heure"].split(":"))
    prevue = maintenant.replace(hour=h, minute=m, second=0, microsecond=0)
    if maintenant < prevue:
        prevue = prevue.fromtimestamp(prevue.timestamp() - 86400)
    if ((d.get("derniere_reussie") or {}).get("fin") or 0) >= prevue.timestamp():
        return False
    # Pas encore réussie depuis l'heure prévue : on (re)tente, au plus une fois par heure.
    return maintenant.timestamp() - (d.get("lancee") or 0) >= 3600


def prochaine(d):
    h, m = (int(x) for x in d["heure"].split(":"))
    maintenant = datetime.now()
    p = maintenant.replace(hour=h, minute=m, second=0, microsecond=0)
    if p <= maintenant:
        p = datetime.fromtimestamp(p.timestamp() + 86400)
    return int(p.timestamp())


# ---------------------------------------------------------------- lire les copies
def env_restic(d, donnees_hote, demo=False):
    return ["env", f"RESTIC_REPOSITORY={depot(d)}", f"RESTIC_PASSWORD_FILE={os.path.join(donnees_hote, 'sauvegardes.cle')}",
            f"RESTIC_CACHE_DIR={'/tmp/restic-jabba-cache' if demo else '/var/cache/restic-jabba'}", "restic", "--no-lock"]


def instantanes(executer, d, donnees_hote, demo=False):
    code, sortie, err = executer(env_restic(d, donnees_hote, demo) + ["snapshots", "--tag", "jabba", "--json"], 120)
    if code != 0:
        raise RuntimeError(err.strip()[-200:] or "Sauvegardes illisibles.")
    liste = json.loads(sortie or "[]")
    liste.sort(key=lambda s: s["time"], reverse=True)
    return [{"id": s["short_id"], "date": s["time"][:19], "chemins": s.get("paths", []),
             "taille": (s.get("summary") or {}).get("total_bytes_processed")} for s in liste]


def parcourir(executer, d, donnees_hote, ident, chemin, demo=False):
    chemin = "/" + chemin.strip("/")
    code, sortie, err = executer(env_restic(d, donnees_hote, demo) + ["ls", "--json", ident, chemin], 120)
    if code != 0:
        raise RuntimeError(err.strip()[-200:] or "Copie illisible.")
    elements = []
    for ligne in sortie.splitlines():
        try:
            n = json.loads(ligne)
        except ValueError:
            continue
        if n.get("struct_type") != "node" or n.get("path") == chemin:
            continue
        if os.path.dirname(n["path"].rstrip("/")) != chemin.rstrip("/") and not (chemin == "/" and n["path"].count("/") == 1):
            continue
        elements.append({"nom": n["name"], "chemin": n["path"], "dossier": n.get("type") == "dir",
                         "taille": n.get("size"), "modifie": (n.get("mtime") or "")[:19]})
    elements.sort(key=lambda e: (not e["dossier"], e["nom"].lower()))
    return elements


def restaurer(executer, d, donnees_hote, ident, chemin, demo=False):
    """Remet `chemin` tel qu'il était dans la copie `ident`, dans Restaurations/<date>/ (jamais par-dessus)."""
    chemin = "/" + chemin.strip("/")
    parent, nom = os.path.dirname(chemin) or "/", os.path.basename(chemin)
    base = os.environ.get("JABBA_DEMO_RESTAURER", "/srv/data/dossiers/Restaurations") if demo else "/srv/data/dossiers/Restaurations"
    cible = os.path.join(base, time.strftime("%Y-%m-%d %Hh%M") + f" ({ident})")
    code, _, err = executer(env_restic(d, donnees_hote, demo) + ["restore", f"{ident}:{parent}", "--target", cible,
                                                                  "--include", "/" + nom], 3600)
    if code != 0:
        raise RuntimeError(err.strip()[-200:] or "Restauration impossible.")
    if not demo:
        executer(["chown", "-R", "1000:1000", base], 600)
    return os.path.join(cible, nom)
