"""ffmpeg et Pillow tournent sans aucun droit (revue, point 1)."""

import os

import pytest
from conftest import exige_root, outil

from services import disk_browser, sandbox, video_player


@exige_root
def test_commande_sous_nobody_sans_capacites():
    r = sandbox.run(
        ["sh", "-c", "id -u; id -G; grep -E '^(CapEff|CapBnd|NoNewPrivs)' /proc/self/status"],
        capture_output=True,
        text=True,
    )
    lignes = r.stdout.split("\n")
    assert lignes[0] == "65534"
    assert lignes[1] in ("65534", "")  # aucun groupe supplémentaire
    statut = dict(ligne.split(":\t") for ligne in lignes[2:] if ligne)
    assert int(statut["CapEff"], 16) == 0 and int(statut["CapBnd"], 16) == 0
    assert statut["NoNewPrivs"].strip() == "1"


@exige_root
def test_ne_lit_pas_les_fichiers_par_leur_chemin(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("clé")
    os.chmod(secret, 0o600)
    r = sandbox.run(["cat", str(secret)], capture_output=True, text=True)
    assert r.returncode != 0 and "clé" not in r.stdout


@exige_root
def test_ne_peut_pas_ecrire_ailleurs(tmp_path):
    os.chmod(tmp_path, 0o755)
    r = sandbox.run(["sh", "-c", f"echo x > {tmp_path}/piege"], capture_output=True)
    assert r.returncode != 0 and not (tmp_path / "piege").exists()


def test_prefixe_vide_sans_root(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert sandbox.prefix() == []


def test_desactivable(monkeypatch):
    monkeypatch.setenv("JABBA_SANS_BAC_A_SABLE", "1")
    assert sandbox.prefix() == []


@outil("ffmpeg")
def test_lecture_complete(video):
    """Analyse, conversion en morceaux et sous-titres : avec le fichier passé déjà ouvert."""
    src = {"entree": video}
    infos = video_player.probe(src)
    assert 5.5 < infos["duree"] < 6.5 and infos["video"]["codec"] == "h264" and infos["soustitres"]
    sid, debut = video_player.start(src, infos, 2)
    assert video_player.session_file(sid, "index.m3u8"), video_player.session_error(sid)
    if sandbox.is_active():
        assert os.stat(os.path.join(video_player.WORK_DIR, sid)).st_uid == sandbox.NOBODY
    vtt = video_player.subtitles(src, infos["soustitres"][0]["index"], 0).decode()
    assert vtt.startswith("WEBVTT") and "Bonjour" in vtt
    video_player.stop(sid)
    assert not os.path.exists(os.path.join(video_player.WORK_DIR, sid))


@outil("ffmpeg")
def test_fichier_illisible(tmp_path):
    f = tmp_path / "faux.mkv"
    f.write_bytes(b"pas une video" * 100)
    with pytest.raises(RuntimeError):
        video_player.probe({"entree": str(f)})


def test_adresse_passee_telle_quelle():
    with sandbox.open_input({"entree": "http://127.0.0.1:8081/f.mkv", "entetes": "Authorization: x\r\n"}) as (a, fds):
        assert a == ["-headers", "Authorization: x\r\n", "-i", "http://127.0.0.1:8081/f.mkv"] and fds == ()


def test_vignette(tmp_path):
    from PIL import Image

    img = tmp_path / "photo.png"
    Image.new("RGB", (1200, 800), "red").save(img)
    jpeg = sandbox.thumbnail(str(img), 128)
    assert jpeg[:3] == b"\xff\xd8\xff"
    cache = disk_browser.thumbnail(str(img), 128)
    assert os.path.getsize(cache) > 100


def test_vignette_fichier_piege(tmp_path):
    f = tmp_path / "piege.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 500)
    with pytest.raises(RuntimeError):
        sandbox.thumbnail(str(f), 128)
    with pytest.raises(disk_browser.Refusal):
        disk_browser.thumbnail(str(f), 128)


def test_entree_refuse_ce_qui_n_est_pas_un_fichier(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "is_active", lambda: True)
    with pytest.raises((RuntimeError, OSError)):
        with sandbox.open_input({"entree": str(tmp_path)}):
            pass
