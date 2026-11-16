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

import config

NOBODY = 65534
GPU_DEVICE = "/dev/dri/renderD128"
IMAGE_MEMORY_LIMIT = 1024**3  # 1 Go au plus pour décoder une image


def is_active():
    """Le bac à sable s'applique quand l'appli tourne en root (le cas normal) ; désactivable pour le développement."""
    return os.geteuid() == 0 and bool(shutil.which("setpriv")) and os.environ.get("JABBA_SANS_BAC_A_SABLE") != "1"


def gpu_group():
    try:
        return os.stat(GPU_DEVICE).st_gid
    except OSError:
        return None


def prefix(gpu=False):
    """Ce qu'on met devant une commande pour la lancer sans droits."""
    if not is_active():
        return []
    group = gpu_group() if gpu else None
    groups_list = [f"--groups={group}"] if group else ["--clear-groups"]
    return [
        "setpriv",
        f"--reuid={NOBODY}",
        f"--regid={NOBODY}",
        *groups_list,
        "--inh-caps=-all",
        "--bounding-set=-all",
        "--no-new-privs",
        "--",
    ]


def local_command(source):
    return not str(source.get("entree", "")).startswith(("http://", "https://"))


@contextmanager
def open_input(source):
    """Arguments d'entrée pour ffmpeg/ffprobe, et les descripteurs à leur passer.

    Fichier local : on l'ouvre ici et on passe le descripteur (-fd N -i fd:), chaque
    appel avec le sien (sa position de lecture lui est propre). Adresse : telle quelle.
    """
    if local_command(source) and is_active():
        descriptor = os.open(source["entree"], os.O_RDONLY)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise RuntimeError("Ce n'est pas un fichier.")
            yield ["-fd", str(descriptor), "-i", "fd:"], (descriptor,)
        finally:
            os.close(descriptor)
    else:
        arguments = ["-headers", source["entetes"]] if source.get("entetes") else []
        yield arguments + ["-i", source["entree"]], ()


def without_input_option(args):
    """ffprobe prend l'entrée sans « -i » devant."""
    arguments = list(args)
    index = arguments.index("-i")
    return arguments[:index] + arguments[index + 1 :]


def run(command, file_descriptors=(), gpu=False, **kw):
    return subprocess.run(prefix(gpu) + command, pass_fds=file_descriptors, **kw)


def popen(command, file_descriptors=(), gpu=False, **kw):
    return subprocess.Popen(prefix(gpu) + command, pass_fds=file_descriptors, **kw)


def writable_dir(path):
    """Dossier où le programme sans droits écrit (morceaux vidéo)."""
    os.makedirs(path, exist_ok=True)
    if is_active():
        parent = os.path.dirname(path)
        os.chmod(parent, os.stat(parent).st_mode | 0o111)  # il doit pouvoir traverser le dossier parent
        os.chown(path, NOBODY, NOBODY)
        os.chmod(path, 0o700)


def _limit_memory():
    resource.setrlimit(resource.RLIMIT_AS, (IMAGE_MEMORY_LIMIT, IMAGE_MEMORY_LIMIT))


def thumbnail(real_path, size, timeout=30):
    """JPEG de l'image `reel` réduite à `taille` px, calculé par Pillow sans droits. Renvoie les octets."""
    # Le code est passé en ligne de commande : le programme sans droits n'a besoin de lire aucun fichier de l'appli.
    with open(os.path.join(config.APP_DIR, "vignette_seule.py")) as file:
        code = file.read()
    with open(real_path, "rb") as file:
        completed = subprocess.run(
            prefix() + [sys.executable, "-I", "-c", code, str(int(size))],
            stdin=file,
            capture_output=True,
            timeout=timeout,
            preexec_fn=_limit_memory,
        )
    if completed.returncode != 0 or not completed.stdout:
        raise RuntimeError("Aperçu impossible.")
    return completed.stdout
