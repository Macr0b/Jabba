"""ffmpeg et Pillow tournent sans aucun droit (revue, point 1)."""

import os
import subprocess

import pytest
from conftest import needs_root, outil

from services import disk_browser, sandbox, video_player


@needs_root
def test_command_as_nobody_without_capabilities():
    r = sandbox.run(
        ["sh", "-c", "id -u; id -G; grep -E '^(CapEff|CapBnd|NoNewPrivs)' /proc/self/status"],
        capture_output=True,
        text=True,
    )
    lines = r.stdout.split("\n")
    assert lines[0] == "65534"
    assert lines[1] in ("65534", "")  # aucun groupe supplémentaire
    status_text = dict(line.split(":\t") for line in lines[2:] if line)
    assert int(status_text["CapEff"], 16) == 0 and int(status_text["CapBnd"], 16) == 0
    assert status_text["NoNewPrivs"].strip() == "1"


@needs_root
def test_does_not_read_files_by_path(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("clé")
    os.chmod(secret, 0o600)
    r = sandbox.run(["cat", str(secret)], capture_output=True, text=True)
    assert r.returncode != 0 and "clé" not in r.stdout


@needs_root
def test_cannot_write_elsewhere(tmp_path):
    os.chmod(tmp_path, 0o755)
    r = sandbox.run(["sh", "-c", f"echo x > {tmp_path}/piege"], capture_output=True)
    assert r.returncode != 0 and not (tmp_path / "piege").exists()


def test_empty_prefix_without_root(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert sandbox.prefix() == []


def test_can_be_disabled(monkeypatch):
    monkeypatch.setenv("JABBA_SANS_BAC_A_SABLE", "1")
    assert sandbox.prefix() == []


@outil("ffmpeg")
def test_full_read(video):
    """Analyse, conversion en morceaux et sous-titres : avec le fichier passé déjà ouvert."""
    src = {"entree": video}
    info = video_player.probe(src)
    assert 5.5 < info["duree"] < 6.5 and info["video"]["codec"] == "h264" and info["soustitres"]
    sid, debut = video_player.start(src, info, 2)
    assert video_player.session_file(sid, "index.m3u8"), video_player.session_error(sid)
    if sandbox.is_active():
        assert os.stat(os.path.join(video_player.WORK_DIR, sid)).st_uid == sandbox.NOBODY
    vtt = video_player.subtitles(src, info["soustitres"][0]["index"], 0).decode()
    assert vtt.startswith("WEBVTT") and "Bonjour" in vtt
    video_player.stop(sid)
    assert not os.path.exists(os.path.join(video_player.WORK_DIR, sid))


@outil("ffmpeg")
def test_unreadable_file(tmp_path):
    f = tmp_path / "faux.mkv"
    f.write_bytes(b"pas une video" * 100)
    with pytest.raises(RuntimeError):
        video_player.probe({"entree": str(f)})


def test_address_passed_as_is():
    with sandbox.open_input({"entree": "http://127.0.0.1:8081/f.mkv", "entetes": "Authorization: x\r\n"}) as (a, fds):
        assert a == ["-headers", "Authorization: x\r\n", "-i", "http://127.0.0.1:8081/f.mkv"] and fds == ()


def test_thumbnail(tmp_path):
    from PIL import Image

    img = tmp_path / "photo.png"
    Image.new("RGB", (1200, 800), "red").save(img)
    jpeg = sandbox.thumbnail(str(img), 128)
    assert jpeg[:3] == b"\xff\xd8\xff"
    cache = disk_browser.thumbnail(str(img), 128)
    assert os.path.getsize(cache) > 100


def test_thumbnail_of_a_trap_file(tmp_path):
    f = tmp_path / "piege.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 500)
    with pytest.raises(RuntimeError):
        sandbox.thumbnail(str(f), 128)
    with pytest.raises(disk_browser.Refusal):
        disk_browser.thumbnail(str(f), 128)


def test_input_refuses_what_is_not_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "is_active", lambda: True)
    with pytest.raises((RuntimeError, OSError)):
        with sandbox.open_input({"entree": str(tmp_path)}):
            pass


@outil("ffmpeg")
def test_avi_converted_and_played(tmp_path):
    """Un AVI (MPEG-4 + MP3, le cas courant) : le navigateur ne sait pas le lire, le serveur le convertit."""
    movie = tmp_path / "vieux film.avi"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=320x240:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=3",
            "-c:v",
            "mpeg4",
            "-c:a",
            "libmp3lame",
            str(movie),
        ],
        check=True,
    )
    src = {"entree": str(movie)}
    info = video_player.probe(src)
    assert info["video"]["codec"] == "mpeg4" and not video_player.plays_directly(info)
    sid, _ = video_player.start(src, info, 0)
    assert video_player.session_file(sid, "index.m3u8"), video_player.session_error(sid)
    video_player.stop(sid)


def test_probe_too_long_clear_message(monkeypatch, tmp_path):
    """Un disque en veille ou un fichier lent : le lecteur affiche la raison au lieu de « format inconnu »."""
    import pytest

    movie = tmp_path / "lent.avi"
    movie.write_bytes(b"x")

    def too_long(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffprobe", video_player.PROBE_SECONDS)

    monkeypatch.setattr(video_player.sandbox, "run", too_long)
    with pytest.raises(RuntimeError, match="Analyse trop longue"):
        video_player.probe({"entree": str(movie)})
