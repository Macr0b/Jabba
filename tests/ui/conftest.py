"""Tests de l'interface : l'appli en démo, ouverte dans un vrai navigateur (Chromium), sur ordinateur et sur iPhone."""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "app")
playwright = pytest.importorskip("playwright.sync_api")


def port_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def appli():
    """Démarre Jabba en démo avec un dossier « Films » contenant une petite vidéo."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg absent")
    d = tempfile.mkdtemp(prefix="jabba-ui-")
    os.chmod(d, 0o711)
    films = os.path.join(d, "films", "Films")
    os.makedirs(films)
    srt = os.path.join(d, "st.srt")
    with open(srt, "w") as f:
        f.write("1\n00:00:01,000 --> 00:00:05,000\nBonjour\n")
    # HEVC (donc converti par le serveur), deux pistes son et des sous-titres : tout le lecteur sert.
    subprocess.run(["ffmpeg", "-loglevel", "quiet", "-f", "lavfi", "-i", "testsrc=duration=20:size=640x360:rate=25",
                    "-f", "lavfi", "-i", "sine=duration=20", "-f", "lavfi", "-i", "sine=duration=20:frequency=880", "-i", srt,
                    "-map", "0", "-map", "1", "-map", "2", "-map", "3",
                    "-c:v", "libx265", "-x265-params", "log-level=none", "-preset", "ultrafast", "-c:a", "aac", "-c:s", "srt",
                    "-metadata:s:a:0", "language=fre", "-metadata:s:a:1", "language=eng",
                    os.path.join(films, "Film test.mkv")], check=True)
    os.makedirs(os.path.join(d, "invites"))
    port = port_libre()
    env = dict(os.environ, JABBA_DEMO="1", TS_USERS="rob@exemple.fr", JABBA_DATA=d, JABBA_ADMIN=f"{d}/admin.json",
               JABBA_JOURNAL=f"{d}/journal.jsonl", JABBA_DONNEES=f"{d}/accueil.json", JABBA_DOSSIER_INVITES=f"{d}/invites",
               JABBA_LECTURE=f"{d}/lecture", JABBA_DEMO_SOURCE="disque", DOSSIERS_DISQUE=f"Films et séries={d}/films",
               JABBA_JOURNAUX_AUTRES="")
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", str(port)], cwd=APP, env=env,
                         stdout=subprocess.DEVNULL, stderr=open(f"{d}/appli.log", "w"))
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/api/moi", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    else:
        p.kill()
        pytest.fail("L'appli ne démarre pas : " + open(f"{d}/appli.log").read()[-500:])
    yield url
    p.terminate()
    p.wait(10)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="session")
def navigateur():
    with playwright.sync_playwright() as pw:
        # Google Chrome sur GitHub (le Chromium de Playwright ne lit pas le H.264) ; ailleurs, celui qui est là.
        canal = os.environ.get("JABBA_UI_NAVIGATEUR")
        b = pw.chromium.launch(**({"channel": canal} if canal else {}))
        yield pw, b
        b.close()


APPAREILS = {"ordinateur": {"viewport": {"width": 1280, "height": 800}}, "iphone": "iPhone 13"}


@pytest.fixture(params=list(APPAREILS))
def page(request, navigateur, appli):
    """Une page connectée en admin, sur ordinateur puis sur iPhone. Toute erreur JavaScript fait échouer le test."""
    pw, b = navigateur
    opts = APPAREILS[request.param]
    ctx = b.new_context(**(pw.devices[opts] if isinstance(opts, str) else opts),
                        permissions=["clipboard-read", "clipboard-write"])
    p = ctx.new_page()
    erreurs = []
    p.on("pageerror", lambda e: erreurs.append(str(e)))
    p.appareil = request.param
    p.goto(appli + "/")
    p.wait_for_selector("#connexion", state="attached")
    p.evaluate("document.querySelector('#connexion').click()")
    p.fill("input[name=identifiant]", "serveur-demo")
    p.fill("input[name=mdp]", "demo-mot-de-passe")
    p.press("input[name=mdp]", "Enter")
    p.wait_for_function("!document.querySelector('dialog[open]')")
    p.wait_for_timeout(500)
    p.racine = appli
    yield p
    ctx.close()
    assert not erreurs, "Erreurs JavaScript : " + " | ".join(erreurs)
