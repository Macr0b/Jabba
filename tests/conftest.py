"""Réglages communs des tests : l'appli tourne en démo, dans un dossier jetable."""
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(RACINE, "app")
sys.path.insert(0, APP)

DONNEES = tempfile.mkdtemp(prefix="jabba-tests-")
os.chmod(DONNEES, 0o711)   # comme /tmp sur le serveur : traversable par le bac à sable
os.environ.update({
    "JABBA_DEMO": "1",
    "TS_USERS": "rob@exemple.fr",
    "JABBA_DATA": DONNEES,
    "JABBA_ADMIN": os.path.join(DONNEES, "admin.json"),
    "JABBA_JOURNAL": os.path.join(DONNEES, "journal.jsonl"),
    "JABBA_JOURNAUX_AUTRES": os.path.join(DONNEES, "porte-wifi", "journal.jsonl"),
    "JABBA_DONNEES": os.path.join(DONNEES, "accueil.json"),
    "JABBA_DOSSIER_INVITES": os.path.join(DONNEES, "dossier-invites"),
    "JABBA_LECTURE": os.path.join(DONNEES, "lecture"),
    "JABBA_VIGNETTES": os.path.join(DONNEES, "vignettes"),
})

EN_ROOT = os.geteuid() == 0
exige_root = pytest.mark.skipif(not EN_ROOT, reason="nécessite root (bac à sable, runuser)")


def outil(nom):
    return pytest.mark.skipif(not shutil.which(nom), reason=f"{nom} absent")


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    """Une petite vidéo de 6 s (h264 + son + sous-titres), fabriquée par ffmpeg."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg absent")
    d = tmp_path_factory.mktemp("video")
    srt = d / "st.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour\n")
    sortie = d / "film.mkv"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=duration=6", "-i", str(srt), "-map", "0", "-map", "1", "-map", "2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "srt",
                    str(sortie)], check=True)
    os.chmod(d, 0o755)
    return str(sortie)
