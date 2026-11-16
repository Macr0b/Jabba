"""Lecteur vidéo de l'appli : lit tous les formats, comme VLC.

Le navigateur ne sait lire que quelques formats (MP4 en H.264, son AAC…).
Pour le reste (MKV, HEVC, son AC3/DTS…), le serveur convertit le film pendant
la lecture avec ffmpeg, en petits morceaux de 4 secondes (HLS) que tous les
navigateurs savent lire : Safari directement, les autres avec hls.js.

- la vidéo déjà en H.264 n'est pas reconvertie (copiée telle quelle) : presque rien à faire ;
- sinon elle est convertie, par la puce graphique du serveur si elle le permet (VAAPI),
  sinon par le processeur, en 1080p au plus ;
- le son est toujours converti en AAC stéréo (léger), avec la piste choisie ;
- les sous-titres texte (SRT, ASS…) sont envoyés à part, en WebVTT.

Aller à un autre moment du film relance la conversion à partir de ce moment.
"""

import json
import os
import secrets
import shutil
import subprocess

from services import sandbox
import threading
import time

WORK_DIR = os.environ.get("JABBA_LECTURE", "/tmp/jabba-lecture")
SEGMENT_SECONDS = 4
MAX_SESSIONS = 3
IDLE_TIMEOUT = 120  # s sans demande de morceau : la conversion s'arrête
TEXT_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}
_SESSIONS = {}
_LOCK = threading.Lock()
_HARDWARE = {"teste": False, "options": None}


# ---------------------------------------------------------------- analyse du fichier
# ffmpeg et ffprobe tournent sans aucun droit (bac_a_sable) : ils ne reçoivent que le
# fichier à lire, déjà ouvert par l'appli.
def probe(source):
    args = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams"]
    with sandbox.open_input(source) as (input_path, file_descriptors):
        completed = sandbox.run(
            args + sandbox.without_input_option(input_path),
            file_descriptors,
            capture_output=True,
            text=True,
            timeout=30,
        )
    if completed.returncode != 0:
        raise RuntimeError("Fichier illisible : " + (completed.stderr.strip()[:200] or "format inconnu"))
    probe_data = json.loads(completed.stdout or "{}")
    streams = probe_data.get("streams", [])
    video_stream = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "video" and not (stream.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )

    def etiquette(stream, count):
        tags = stream.get("tags") or {}
        shares = [
            tags.get("title"),
            (tags.get("language") or "").upper() if tags.get("language") not in (None, "und") else None,
        ]
        return " · ".join(minutes for minutes in shares if minutes) or f"Piste {count}"

    audio_tracks = [
        {
            "index": stream["index"],
            "codec": stream.get("codec_name"),
            "canaux": stream.get("channels"),
            "nom": etiquette(stream, index + 1),
            "defaut": bool((stream.get("disposition") or {}).get("default")),
        }
        for index, stream in enumerate(value for value in streams if value.get("codec_type") == "audio")
    ]
    soustitres = [
        {
            "index": stream["index"],
            "codec": stream.get("codec_name"),
            "nom": etiquette(stream, index + 1),
            "texte": stream.get("codec_name") in TEXT_SUBTITLE_CODECS,
        }
        for index, stream in enumerate(value for value in streams if value.get("codec_type") == "subtitle")
    ]
    format_name = probe_data.get("format", {})
    return {
        "duree": float(format_name.get("duration") or 0),
        "conteneur": format_name.get("format_name", ""),
        "video": {
            "codec": video_stream.get("codec_name"),
            "largeur": video_stream.get("width"),
            "hauteur": video_stream.get("height"),
            "pix_fmt": video_stream.get("pix_fmt"),
            "index": video_stream["index"],
        }
        if video_stream
        else None,
        "audios": audio_tracks,
        "soustitres": soustitres,
    }


def plays_directly(media):
    """Le navigateur sait-il lire le fichier tel quel ?"""
    video_stream, first = media["video"], media["audios"]
    container_ok = any(
        container_format in media["conteneur"] for container_format in ("mp4", "mov")
    )  # MKV (même « matroska,webm ») : converti
    video_ok = (
        video_stream is not None
        and video_stream["codec"] in ("h264", "vp9", "av1")
        and video_stream["pix_fmt"] in (None, "yuv420p")
    )
    audio_ok = not first or all(value["codec"] in ("aac", "mp3", "opus", "vorbis") for value in first[:1])
    return container_ok and video_ok and audio_ok and len(first) <= 1


# ---------------------------------------------------------------- conversion
def _hardware_options():
    """Conversion par la puce graphique (VAAPI) si elle marche sur ce serveur, sinon None."""
    if _HARDWARE["teste"]:
        return _HARDWARE["options"]
    _HARDWARE["teste"] = True
    if not os.path.exists("/dev/dri/renderD128") or not shutil.which("ffmpeg"):
        return None
    for driver in (None, "i965", "iHD"):
        env = dict(os.environ)
        if driver:
            env["LIBVA_DRIVER_NAME"] = driver
        attempt = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-vaapi_device",
            "/dev/dri/renderD128",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=640x360:rate=25",
            "-vf",
            "format=nv12,hwupload",
            "-c:v",
            "h264_vaapi",
            "-f",
            "null",
            "-",
        ]
        try:
            if sandbox.run(attempt, gpu=True, capture_output=True, env=env, timeout=30).returncode == 0:
                _HARDWARE["options"] = {"pilote": driver}
                return _HARDWARE["options"]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def _video(media):
    video_stream = media["video"]
    if video_stream is None:
        return [], {}
    if video_stream["codec"] == "h264" and video_stream["pix_fmt"] in (None, "yuv420p"):
        return ["-map", f"0:{video_stream['index']}", "-c:v", "copy"], {}
    scale = "scale=-2:'min(1080,ih)'"
    hardware = _hardware_options()
    if hardware is not None:
        env = {"LIBVA_DRIVER_NAME": hardware["pilote"]} if hardware["pilote"] else {}
        return (
            [
                "-vaapi_device",
                "/dev/dri/renderD128",
                "-map",
                f"0:{video_stream['index']}",
                "-vf",
                f"{scale},format=nv12,hwupload",
                "-c:v",
                "h264_vaapi",
                "-qp",
                "24",
                "-g",
                str(SEGMENT_SECONDS * 25),
            ],
            env,
        )
    return (
        [
            "-map",
            f"0:{video_stream['index']}",
            "-vf",
            f"{scale},format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-g",
            str(SEGMENT_SECONDS * 25),
            "-sc_threshold",
            "0",
        ],
        {},
    )


def keyframe(source, media, debut):
    """Quand la vidéo est copiée, elle ne peut repartir que d'une image clé : celle juste avant `debut`.
    On part donc de là, pour que le temps affiché et les sous-titres tombent juste."""
    video_stream = media["video"]
    if (
        debut <= 0
        or not video_stream
        or not (video_stream["codec"] == "h264" and video_stream["pix_fmt"] in (None, "yuv420p"))
    ):
        return debut
    args = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        f"{video_stream['index']}",
        "-skip_frame",
        "nokey",
        "-show_entries",
        "frame=pts_time,best_effort_timestamp_time",
        "-of",
        "csv=p=0",
        "-read_intervals",
        f"{max(0.0, debut - 12):.3f}%{debut + 0.05:.3f}",
    ]
    try:
        with sandbox.open_input(source) as (input_path, file_descriptors):
            completed = sandbox.run(
                args + sandbox.without_input_option(input_path),
                file_descriptors,
                capture_output=True,
                text=True,
                timeout=20,
            )
    except (subprocess.TimeoutExpired, OSError, RuntimeError):
        return debut
    elapsed = []
    for line in completed.stdout.splitlines():
        for share in line.split(","):
            try:
                elapsed.append(float(share))
                break
            except ValueError:
                continue
    previous_value = [tags for tags in elapsed if tags <= debut + 0.05]
    return max(previous_value) if previous_value else debut


def start(source, media, debut=0.0, audio=None):
    """Lance la conversion à partir de `debut` (secondes). Renvoie (identifiant, moment réel du départ)."""
    debut = keyframe(source, media, max(0.0, float(debut)))
    with _LOCK:
        while len(_SESSIONS) >= MAX_SESSIONS:  # trop de films en même temps : on arrête le plus ancien
            stop(min(_SESSIONS, key=lambda key: _SESSIONS[key]["vu"]), lock=False)
    session_id = secrets.token_urlsafe(18)
    folder = os.path.join(WORK_DIR, session_id)
    os.makedirs(WORK_DIR, exist_ok=True)
    sandbox.writable_dir(folder)
    video_stream, extra_env = _video(media)
    tracks = [first for first in media["audios"] if audio is None or first["index"] == audio] or media["audios"]
    audio_stream = ["-map", f"0:{tracks[0]['index']}", "-c:a", "aac", "-b:a", "192k", "-ac", "2"] if tracks else []
    command = (
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, float(debut)):.3f}"]
        + ([first for first in video_stream[:2]] if video_stream[:1] == ["-vaapi_device"] else [])
        + ["ENTREE"]
        + (video_stream[2:] if video_stream[:1] == ["-vaapi_device"] else video_stream)
        + audio_stream
        + (
            ["-force_key_frames", "expr:gte(t,n_forced*2)"]
            if video_stream[-2:] != ["-c:v", "copy"] and "copy" not in video_stream
            else []
        )
        # horodatages à partir de 0 : la vidéo et les sous-titres partent du même instant, sur tous les navigateurs
        + ["-muxdelay", "0", "-muxpreload", "0"]
        + [
            "-max_muxing_queue_size",
            "4096",
            "-f",
            "hls",
            "-hls_init_time",
            "1",
            "-hls_time",
            str(SEGMENT_SECONDS),
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "event",
            "-hls_segment_filename",
            os.path.join(folder, "m%05d.ts"),
            os.path.join(folder, "index.m3u8"),
        ]
    )
    env = dict(os.environ, **extra_env)
    with (
        sandbox.open_input(source) as (input_path, file_descriptors),
        open(os.path.join(folder, "erreurs.txt"), "w") as error_output,
    ):
        index = command.index("ENTREE")
        command[index : index + 1] = input_path
        process = sandbox.popen(
            command,
            file_descriptors,
            gpu=bool(extra_env) or "-vaapi_device" in command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=error_output,
            env=env,
        )
    with _LOCK:
        _SESSIONS[session_id] = {
            "p": process,
            "dossier": folder,
            "vu": time.time(),
            "cree": time.time(),
            "debut": float(debut),
        }
    return session_id, debut


def session_file(session_id, name, wait=20):
    """Chemin d'un morceau ou de la liste de lecture, une fois prêt."""
    stream = _SESSIONS.get(session_id)
    if not stream or not (
        name == "index.m3u8" or (name.startswith("m") and name.endswith(".ts") and name[1:-3].isdigit())
    ):
        return None
    stream["vu"] = time.time()
    path = os.path.join(stream["dossier"], name)
    end = time.time() + wait
    while time.time() < end:
        if name == "index.m3u8":
            # Au démarrage, la liste n'est servie qu'avec quelques morceaux d'avance (6, ou moins si la
            # conversion traîne) : le lecteur ne la relit qu'au rythme des morceaux et s'arrêterait entre deux.
            try:
                text = open(path).read()
                count = text.count(".ts")
                age = time.time() - stream["cree"]
                if count >= 6 or "#EXT-X-ENDLIST" in text or (count >= 2 and age > 3) or (count >= 1 and age > 8):
                    return path
            except OSError:
                pass
        elif os.path.exists(path):
            next_value = os.path.join(stream["dossier"], f"m{int(name[1:-3]) + 1:05d}.ts")
            if os.path.exists(next_value) or stream["p"].poll() is not None:  # morceau terminé
                return path
        if stream["p"].poll() not in (None, 0) and not os.path.exists(path):
            return None
        time.sleep(0.25)
    return None


def session_error(session_id):
    stream = _SESSIONS.get(session_id)
    if not stream:
        return "Lecture arrêtée."
    if stream["p"].poll() not in (None, 0):
        try:
            return (
                open(os.path.join(stream["dossier"], "erreurs.txt")).read().strip()[-300:]
                or "La conversion s'est arrêtée."
            )
        except OSError:
            return "La conversion s'est arrêtée."
    return None


def stop(session_id, lock=True):
    def faire():
        stream = _SESSIONS.pop(session_id, None)
        if not stream:
            return
        if stream["p"].poll() is None:
            stream["p"].terminate()
            try:
                stream["p"].wait(timeout=5)
            except subprocess.TimeoutExpired:
                stream["p"].kill()
        shutil.rmtree(stream["dossier"], ignore_errors=True)

    if lock:
        with _LOCK:
            faire()
    else:
        faire()


def clean_up():
    """Arrête les lectures abandonnées (onglet fermé, téléphone en veille…)."""
    cutoff = time.time() - IDLE_TIMEOUT
    for session_id in [key for key, stream in list(_SESSIONS.items()) if stream["vu"] < cutoff]:
        stop(session_id)


def _seconds(tags):
    shares = tags.replace(",", ".").split(":")
    return sum(float(minutes) * 60**key for key, minutes in enumerate(reversed(shares)))


def _timestamp(value):
    hours, rest = divmod(max(0.0, value), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{seconds:06.3f}"


def shift_vtt(vtt_text, debut):
    """Recale les sous-titres sur le moment où la lecture a commencé (la vidéo, elle, repart de 0)."""
    output, chunk = ["WEBVTT", ""], []
    for line in vtt_text.splitlines() + [""]:
        if line.strip():
            chunk.append(line)
            continue
        timing = next((chunk_line for chunk_line in chunk if "-->" in chunk_line), None)
        if timing:
            first, _, second = timing.partition("-->")
            cue_end = _seconds(second.split()[0]) - debut
            if cue_end > 0:
                text = chunk[chunk.index(timing) + 1 :]
                output += [f"{_timestamp(_seconds(first.strip()) - debut)} --> {_timestamp(cue_end)}"] + text + [""]
        chunk = []
    return "\n".join(output)


def subtitles(source, index, debut=0.0):
    """Une piste de sous-titres texte en WebVTT, calée sur le moment où la lecture a commencé."""
    with sandbox.open_input(source) as (input_path, file_descriptors):
        command = (
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
            + input_path
            + ["-map", f"0:{int(index)}", "-f", "webvtt", "-"]
        )
        completed = sandbox.run(command, file_descriptors, capture_output=True, timeout=180)
    if completed.returncode != 0:
        raise RuntimeError("Sous-titres illisibles.")
    return shift_vtt(completed.stdout.decode("utf-8", "replace"), float(debut)).encode()
