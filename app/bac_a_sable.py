"""Bac à sable : ffmpeg et Pillow tournent sans aucun droit.

Les vidéos et les images peuvent venir de n'importe qui (un invité qui dépose un
fichier, un téléchargement…). Un fichier piégé qui exploiterait une faille de
ffmpeg ou de Pillow ne doit rien pouvoir faire. Ces programmes tournent donc :
- sous le compte « nobody » (65534), sans aucun groupe (sauf celui de la puce
  graphique pour ffmpeg, s'il y en a une) ;
- sans aucune capacité Linux, et sans pouvoir en regagner (no_new_privs) ;
- sans accès aux fichiers du serveur : l'appli ouvre elle-même le fichier à lire
  et ne leur passe que ce fichier-là, déjà ouvert (descripteur hérité).

L'appli vérifie le chemin avant d'ouvrir (comme pour tout fichier), puis le
programme sans droits ne voit que ce qu'on lui a passé.
"""
import os
import resource
import shutil
import stat
import subprocess
import sys
from contextlib import contextmanager

NOBODY = 65534
PUCE = "/dev/dri/renderD128"
MEMOIRE_IMAGE = 1024 ** 3      # 1 Go au plus pour décoder une image


def actif():
    """Le bac à sable s'applique quand l'appli tourne en root (le cas normal) ; désactivable pour le développement."""
    return os.geteuid() == 0 and bool(shutil.which("setpriv")) and os.environ.get("JABBA_SANS_BAC_A_SABLE") != "1"


def groupe_puce():
    try:
        return os.stat(PUCE).st_gid
    except OSError:
        return None


def prefixe(puce=False):
    """Ce qu'on met devant une commande pour la lancer sans droits."""
    if not actif():
        return []
    g = groupe_puce() if puce else None
    groupes = [f"--groups={g}"] if g else ["--clear-groups"]
    return ["setpriv", f"--reuid={NOBODY}", f"--regid={NOBODY}", *groupes,
            "--inh-caps=-all", "--bounding-set=-all", "--no-new-privs", "--"]


def local(source):
    return not str(source.get("entree", "")).startswith(("http://", "https://"))


@contextmanager
def entree(source):
    """Arguments d'entrée pour ffmpeg/ffprobe, et les descripteurs à leur passer.

    Fichier local : on l'ouvre ici et on passe le descripteur (-fd N -i fd:), chaque
    appel avec le sien (sa position de lecture lui est propre). Adresse : telle quelle.
    """
    if local(source) and actif():
        fd = os.open(source["entree"], os.O_RDONLY)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise RuntimeError("Ce n'est pas un fichier.")
            yield ["-fd", str(fd), "-i", "fd:"], (fd,)
        finally:
            os.close(fd)
    else:
        a = ["-headers", source["entetes"]] if source.get("entetes") else []
        yield a + ["-i", source["entree"]], ()


def sans_i(args):
    """ffprobe prend l'entrée sans « -i » devant."""
    a = list(args)
    i = a.index("-i")
    return a[:i] + a[i + 1:]


def run(cmd, fds=(), puce=False, **kw):
    return subprocess.run(prefixe(puce) + cmd, pass_fds=fds, **kw)


def popen(cmd, fds=(), puce=False, **kw):
    return subprocess.Popen(prefixe(puce) + cmd, pass_fds=fds, **kw)


def dossier_ecrivable(chemin):
    """Dossier où le programme sans droits écrit (morceaux vidéo)."""
    os.makedirs(chemin, exist_ok=True)
    if actif():
        parent = os.path.dirname(chemin)
        os.chmod(parent, os.stat(parent).st_mode | 0o111)   # il doit pouvoir traverser le dossier parent
        os.chown(chemin, NOBODY, NOBODY)
        os.chmod(chemin, 0o700)


def _limiter_memoire():
    resource.setrlimit(resource.RLIMIT_AS, (MEMOIRE_IMAGE, MEMOIRE_IMAGE))


def vignette(reel, taille, timeout=30):
    """JPEG de l'image `reel` réduite à `taille` px, calculé par Pillow sans droits. Renvoie les octets."""
    # Le code est passé en ligne de commande : le programme sans droits n'a besoin de lire aucun fichier de l'appli.
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "vignette_seule.py")) as f:
        code = f.read()
    with open(reel, "rb") as f:
        r = subprocess.run(prefixe() + [sys.executable, "-I", "-c", code, str(int(taille))], stdin=f,
                           capture_output=True, timeout=timeout, preexec_fn=_limiter_memoire)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("Aperçu impossible.")
    return r.stdout
