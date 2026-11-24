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

Sommaire :
  probe           : ce que contient une vidéo (durée, pistes), lu par ffprobe dans le bac à sable
  plays_directly  : le navigateur sait-il lire le fichier tel quel ?
  keyframe        : quand la vidéo est copiée, elle ne peut repartir que d'une image clé : celle juste avant `debut`
  start           : lance la conversion à partir de `debut` (secondes). Renvoie (identifiant, moment réel du départ)
  session_file    : chemin d'un morceau ou de la liste de lecture, une fois prêt
  session_error   : pourquoi une lecture s'est arrêtée, en une phrase
  stop            : arrête une lecture et efface ses morceaux
  clean_up        : arrête les lectures abandonnées (onglet fermé, téléphone en veille…)
  shift_vtt       : recale les sous-titres sur le moment où la lecture a commencé (la vidéo, elle, repart de 0)
  subtitles       : une piste de sous-titres texte en WebVTT, calée sur le moment où la lecture a commencé
  HLS_OPTIONS     : les réglages du flux découpé en morceaux (HLS)
  interne : _run_ffprobe, _track_label, _tracks, _video_track, _hardware_options, _video, _ffmpeg_command, _seconds, _timestamp
"""

import json
import os
import secrets
import shutil
import subprocess
import threading
import time

from services import sandbox

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
PROBE_SECONDS = 90  # analyse d'un fichier avant lecture


def _run_ffprobe(source):
    """Ce que ffprobe dit du fichier (dans le bac à sable), ou une erreur en clair."""
    args = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams"]
    try:
        with sandbox.open_input(source) as (input_path, file_descriptors):
            completed = sandbox.run(
                args + sandbox.without_input_option(input_path),
                file_descriptors,
                capture_output=True,
                text=True,
                timeout=PROBE_SECONDS,
            )
    except subprocess.TimeoutExpired:
        # Un disque USB en veille met plusieurs secondes à repartir ; un vieil AVI peut être lu en entier.
        raise RuntimeError(
            f"Analyse trop longue (plus de {PROBE_SECONDS} s) : disque en veille ou fichier très lent à lire."
        )
    except FileNotFoundError:
        raise  # ffprobe absent de l'appli : message à part (routes/player.py)
    except OSError as error:
        raise RuntimeError(f"Fichier impossible à ouvrir : {error.strerror or error}")
    if completed.returncode != 0:
        raise RuntimeError("Fichier illisible : " + (completed.stderr.strip()[:200] or "format inconnu"))
    return json.loads(completed.stdout or "{}")


def _track_label(stream, number):
    """« Titre · FR », ou « Piste 2 » s'il n'y a ni titre ni langue."""
    tags = stream.get("tags") or {}
    language = tags.get("language")
    parts = [tags.get("title"), language.upper() if language not in (None, "und") else None]
    return " · ".join(part for part in parts if part) or f"Piste {number}"


def _tracks(streams, kind):
    return [stream for stream in streams if stream.get("codec_type") == kind]


def _video_track(streams):
    """La piste vidéo (pas une pochette), ou None pour un fichier audio."""
    for stream in _tracks(streams, "video"):
        if not (stream.get("disposition") or {}).get("attached_pic"):
            return {
                "codec": stream.get("codec_name"),
                "largeur": stream.get("width"),
                "hauteur": stream.get("height"),
                "pix_fmt": stream.get("pix_fmt"),
                "index": stream["index"],
            }
    return None


def probe(source):
    probe_data = _run_ffprobe(source)
    streams = probe_data.get("streams", [])
    audio_tracks = [
        {
            "index": stream["index"],
            "codec": stream.get("codec_name"),
            "canaux": stream.get("channels"),
            "nom": _track_label(stream, number),
            "defaut": bool((stream.get("disposition") or {}).get("default")),
        }
        for number, stream in enumerate(_tracks(streams, "audio"), start=1)
    ]
    subtitle_tracks = [
        {
            "index": stream["index"],
            "codec": stream.get("codec_name"),
            "nom": _track_label(stream, number),
            "texte": stream.get("codec_name") in TEXT_SUBTITLE_CODECS,
        }
        for number, stream in enumerate(_tracks(streams, "subtitle"), start=1)
    ]
    container = probe_data.get("format", {})
    return {
        "duree": float(container.get("duration") or 0),
        "conteneur": container.get("format_name", ""),
        "video": _video_track(streams),
        "audios": audio_tracks,
        "soustitres": subtitle_tracks,
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


def keyframe(source, media, start_time):
    """Quand la vidéo est copiée, elle ne peut repartir que d'une image clé : celle juste avant `debut`.
    On part donc de là, pour que le temps affiché et les sous-titres tombent juste."""
    video_stream = media["video"]
    if (
        start_time <= 0
        or not video_stream
        or not (video_stream["codec"] == "h264" and video_stream["pix_fmt"] in (None, "yuv420p"))
    ):
        return start_time
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
        f"{max(0.0, start_time - 12):.3f}%{start_time + 0.05:.3f}",
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
        return start_time
    elapsed = []
    for line in completed.stdout.splitlines():
        for share in line.split(","):
            try:
                elapsed.append(float(share))
                break
            except ValueError:
                continue
    previous_value = [tags for tags in elapsed if tags <= start_time + 0.05]
    return max(previous_value) if previous_value else start_time


# Le flux HLS : des morceaux de SEGMENT_SECONDS, horodatés à partir de 0 (la vidéo et les sous-titres
# partent du même instant, sur tous les navigateurs).
HLS_OPTIONS = ["-muxdelay", "0", "-muxpreload", "0", "-max_muxing_queue_size", "4096", "-f", "hls"]
HLS_OPTIONS += ["-hls_init_time", "1", "-hls_time", str(SEGMENT_SECONDS), "-hls_list_size", "0"]
HLS_OPTIONS += ["-hls_playlist_type", "event"]


def _ffmpeg_command(media, start_time, audio, folder):
    """La conversion : « ENTREE » marque la place du fichier (ouvert ensuite dans le bac à sable)."""
    video_options, extra_env = _video(media)
    tracks = [track for track in media["audios"] if audio is None or track["index"] == audio] or media["audios"]
    audio_options = ["-map", f"0:{tracks[0]['index']}", "-c:a", "aac", "-b:a", "192k", "-ac", "2"] if tracks else []
    # Le décodage par la puce graphique (VAAPI) se règle avant le fichier d'entrée, le reste après.
    before_input = video_options[:2] if video_options[:1] == ["-vaapi_device"] else []
    after_input = video_options[len(before_input) :]
    copying = video_options[-2:] == ["-c:v", "copy"] or "copy" in video_options
    keyframes = [] if copying else ["-force_key_frames", "expr:gte(t,n_forced*2)"]
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{start_time:.3f}"]
    command += before_input + ["ENTREE"] + after_input + audio_options + keyframes + HLS_OPTIONS
    command += ["-hls_segment_filename", os.path.join(folder, "m%05d.ts"), os.path.join(folder, "index.m3u8")]
    return command, extra_env


def start(source, media, start_time=0.0, audio=None):
    """Lance la conversion à partir de `debut` (secondes). Renvoie (identifiant, moment réel du départ)."""
    start_time = keyframe(source, media, max(0.0, float(start_time)))
    with _LOCK:
        while len(_SESSIONS) >= MAX_SESSIONS:  # trop de films en même temps : on arrête le plus ancien
            stop(min(_SESSIONS, key=lambda key: _SESSIONS[key]["vu"]), lock=False)
    session_id = secrets.token_urlsafe(18)
    folder = os.path.join(WORK_DIR, session_id)
    os.makedirs(WORK_DIR, exist_ok=True)
    sandbox.writable_dir(folder)
    command, extra_env = _ffmpeg_command(media, max(0.0, float(start_time)), audio, folder)
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
            env=dict(os.environ, **extra_env),
        )
    with _LOCK:
        _SESSIONS[session_id] = {
            "p": process,
            "dossier": folder,
            "vu": time.time(),
            "cree": time.time(),
            "debut": float(start_time),
        }
    return session_id, start_time


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


def shift_vtt(vtt_text, start_time):
    """Recale les sous-titres sur le moment où la lecture a commencé (la vidéo, elle, repart de 0)."""
    output, chunk = ["WEBVTT", ""], []
    for line in vtt_text.splitlines() + [""]:
        if line.strip():
            chunk.append(line)
            continue
        timing = next((chunk_line for chunk_line in chunk if "-->" in chunk_line), None)
        if timing:
            first, _, second = timing.partition("-->")
            cue_end = _seconds(second.split()[0]) - start_time
            if cue_end > 0:
                text = chunk[chunk.index(timing) + 1 :]
                output += (
                    [f"{_timestamp(_seconds(first.strip()) - start_time)} --> {_timestamp(cue_end)}"] + text + [""]
                )
        chunk = []
    return "\n".join(output)


def subtitles(source, index, start_time=0.0):
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
    return shift_vtt(completed.stdout.decode("utf-8", "replace"), float(start_time)).encode()
