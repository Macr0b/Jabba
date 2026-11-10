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

import bac_a_sable
import threading
import time

RACINE = os.environ.get("JABBA_LECTURE", "/tmp/jabba-lecture")
DUREE_MORCEAU = 4
MAX_SESSIONS = 3
INACTIVITE = 120          # s sans demande de morceau : la conversion s'arrête
SOUS_TITRES_TEXTE = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}
_SESSIONS = {}
_VERROU = threading.Lock()
_MATERIEL = {"teste": False, "options": None}


# ---------------------------------------------------------------- analyse du fichier
# ffmpeg et ffprobe tournent sans aucun droit (bac_a_sable) : ils ne reçoivent que le
# fichier à lire, déjà ouvert par l'appli.
def sonder(source):
    args = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams"]
    with bac_a_sable.entree(source) as (entree, fds):
        r = bac_a_sable.run(args + bac_a_sable.sans_i(entree), fds, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError("Fichier illisible : " + (r.stderr.strip()[:200] or "format inconnu"))
    d = json.loads(r.stdout or "{}")
    flux = d.get("streams", [])
    video = next((s for s in flux if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)

    def etiquette(s, n):
        t = s.get("tags") or {}
        morceaux = [t.get("title"), (t.get("language") or "").upper() if t.get("language") not in (None, "und") else None]
        return " · ".join(m for m in morceaux if m) or f"Piste {n}"

    audios = [{"index": s["index"], "codec": s.get("codec_name"), "canaux": s.get("channels"),
               "nom": etiquette(s, i + 1), "defaut": bool((s.get("disposition") or {}).get("default"))}
              for i, s in enumerate(x for x in flux if x.get("codec_type") == "audio")]
    soustitres = [{"index": s["index"], "codec": s.get("codec_name"), "nom": etiquette(s, i + 1),
                   "texte": s.get("codec_name") in SOUS_TITRES_TEXTE}
                  for i, s in enumerate(x for x in flux if x.get("codec_type") == "subtitle")]
    fmt = d.get("format", {})
    return {
        "duree": float(fmt.get("duration") or 0),
        "conteneur": fmt.get("format_name", ""),
        "video": {"codec": video.get("codec_name"), "largeur": video.get("width"), "hauteur": video.get("height"),
                  "pix_fmt": video.get("pix_fmt"), "index": video["index"]} if video else None,
        "audios": audios,
        "soustitres": soustitres,
    }


def lecture_directe(infos):
    """Le navigateur sait-il lire le fichier tel quel ?"""
    v, a = infos["video"], infos["audios"]
    conteneur_ok = any(c in infos["conteneur"] for c in ("mp4", "mov"))   # MKV (même « matroska,webm ») : converti
    video_ok = v is not None and v["codec"] in ("h264", "vp9", "av1") and v["pix_fmt"] in (None, "yuv420p")
    son_ok = not a or all(x["codec"] in ("aac", "mp3", "opus", "vorbis") for x in a[:1])
    return conteneur_ok and video_ok and son_ok and len(a) <= 1


# ---------------------------------------------------------------- conversion
def _options_materiel():
    """Conversion par la puce graphique (VAAPI) si elle marche sur ce serveur, sinon None."""
    if _MATERIEL["teste"]:
        return _MATERIEL["options"]
    _MATERIEL["teste"] = True
    if not os.path.exists("/dev/dri/renderD128") or not shutil.which("ffmpeg"):
        return None
    for pilote in (None, "i965", "iHD"):
        env = dict(os.environ)
        if pilote:
            env["LIBVA_DRIVER_NAME"] = pilote
        essai = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-vaapi_device", "/dev/dri/renderD128",
                 "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=25",
                 "-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-f", "null", "-"]
        try:
            if bac_a_sable.run(essai, puce=True, capture_output=True, env=env, timeout=30).returncode == 0:
                _MATERIEL["options"] = {"pilote": pilote}
                return _MATERIEL["options"]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def _video(infos):
    v = infos["video"]
    if v is None:
        return [], {}
    if v["codec"] == "h264" and v["pix_fmt"] in (None, "yuv420p"):
        return ["-map", f"0:{v['index']}", "-c:v", "copy"], {}
    echelle = "scale=-2:'min(1080,ih)'"
    hw = _options_materiel()
    if hw is not None:
        env = {"LIBVA_DRIVER_NAME": hw["pilote"]} if hw["pilote"] else {}
        return (["-vaapi_device", "/dev/dri/renderD128", "-map", f"0:{v['index']}",
                 "-vf", f"{echelle},format=nv12,hwupload", "-c:v", "h264_vaapi", "-qp", "24",
                 "-g", str(DUREE_MORCEAU * 25)], env)
    return (["-map", f"0:{v['index']}", "-vf", f"{echelle},format=yuv420p", "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "23", "-g", str(DUREE_MORCEAU * 25), "-sc_threshold", "0"], {})


def image_cle(source, infos, debut):
    """Quand la vidéo est copiée, elle ne peut repartir que d'une image clé : celle juste avant `debut`.
    On part donc de là, pour que le temps affiché et les sous-titres tombent juste."""
    v = infos["video"]
    if debut <= 0 or not v or not (v["codec"] == "h264" and v["pix_fmt"] in (None, "yuv420p")):
        return debut
    args = ["ffprobe", "-v", "error", "-select_streams", f"{v['index']}", "-skip_frame", "nokey",
            "-show_entries", "frame=pts_time,best_effort_timestamp_time", "-of", "csv=p=0",
            "-read_intervals", f"{max(0.0, debut - 12):.3f}%{debut + 0.05:.3f}"]
    try:
        with bac_a_sable.entree(source) as (entree, fds):
            r = bac_a_sable.run(args + bac_a_sable.sans_i(entree), fds, capture_output=True, text=True, timeout=20)
    except (subprocess.TimeoutExpired, OSError, RuntimeError):
        return debut
    temps = []
    for ligne in r.stdout.splitlines():
        for morceau in ligne.split(","):
            try:
                temps.append(float(morceau))
                break
            except ValueError:
                continue
    avant = [t for t in temps if t <= debut + 0.05]
    return max(avant) if avant else debut


def demarrer(source, infos, debut=0.0, audio=None):
    """Lance la conversion à partir de `debut` (secondes). Renvoie (identifiant, moment réel du départ)."""
    debut = image_cle(source, infos, max(0.0, float(debut)))
    with _VERROU:
        while len(_SESSIONS) >= MAX_SESSIONS:   # trop de films en même temps : on arrête le plus ancien
            arreter(min(_SESSIONS, key=lambda k: _SESSIONS[k]["vu"]), verrou=False)
    sid = secrets.token_urlsafe(18)
    dossier = os.path.join(RACINE, sid)
    os.makedirs(RACINE, exist_ok=True)
    bac_a_sable.dossier_ecrivable(dossier)
    video, env_sup = _video(infos)
    pistes = [a for a in infos["audios"] if audio is None or a["index"] == audio] or infos["audios"]
    son = ["-map", f"0:{pistes[0]['index']}", "-c:a", "aac", "-b:a", "192k", "-ac", "2"] if pistes else []
    cmd = (["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, float(debut)):.3f}"]
           + ([a for a in video[:2]] if video[:1] == ["-vaapi_device"] else [])
           + ["ENTREE"]
           + (video[2:] if video[:1] == ["-vaapi_device"] else video)
           + son
           + (["-force_key_frames", "expr:gte(t,n_forced*2)"] if video[-2:] != ["-c:v", "copy"] and "copy" not in video else [])
           # horodatages à partir de 0 : la vidéo et les sous-titres partent du même instant, sur tous les navigateurs
           + ["-muxdelay", "0", "-muxpreload", "0"]
           + ["-max_muxing_queue_size", "4096", "-f", "hls", "-hls_init_time", "1", "-hls_time", str(DUREE_MORCEAU),
              "-hls_list_size", "0", "-hls_playlist_type", "event",
              "-hls_segment_filename", os.path.join(dossier, "m%05d.ts"), os.path.join(dossier, "index.m3u8")])
    env = dict(os.environ, **env_sup)
    with bac_a_sable.entree(source) as (entree, fds), open(os.path.join(dossier, "erreurs.txt"), "w") as err:
        i = cmd.index("ENTREE")
        cmd[i:i + 1] = entree
        p = bac_a_sable.popen(cmd, fds, puce=bool(env_sup) or "-vaapi_device" in cmd, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=err, env=env)
    with _VERROU:
        _SESSIONS[sid] = {"p": p, "dossier": dossier, "vu": time.time(), "cree": time.time(), "debut": float(debut)}
    return sid, debut


def fichier(sid, nom, attendre=20):
    """Chemin d'un morceau ou de la liste de lecture, une fois prêt."""
    s = _SESSIONS.get(sid)
    if not s or not (nom == "index.m3u8" or (nom.startswith("m") and nom.endswith(".ts") and nom[1:-3].isdigit())):
        return None
    s["vu"] = time.time()
    chemin = os.path.join(s["dossier"], nom)
    fin = time.time() + attendre
    while time.time() < fin:
        if nom == "index.m3u8":
            # Au démarrage, la liste n'est servie qu'avec quelques morceaux d'avance (6, ou moins si la
            # conversion traîne) : le lecteur ne la relit qu'au rythme des morceaux et s'arrêterait entre deux.
            try:
                texte = open(chemin).read()
                n = texte.count(".ts")
                age = time.time() - s["cree"]
                if n >= 6 or "#EXT-X-ENDLIST" in texte or (n >= 2 and age > 3) or (n >= 1 and age > 8):
                    return chemin
            except OSError:
                pass
        elif os.path.exists(chemin):
            suivant = os.path.join(s["dossier"], f"m{int(nom[1:-3]) + 1:05d}.ts")
            if os.path.exists(suivant) or s["p"].poll() is not None:   # morceau terminé
                return chemin
        if s["p"].poll() not in (None, 0) and not os.path.exists(chemin):
            return None
        time.sleep(0.25)
    return None


def erreur(sid):
    s = _SESSIONS.get(sid)
    if not s:
        return "Lecture arrêtée."
    if s["p"].poll() not in (None, 0):
        try:
            return open(os.path.join(s["dossier"], "erreurs.txt")).read().strip()[-300:] or "La conversion s'est arrêtée."
        except OSError:
            return "La conversion s'est arrêtée."
    return None


def arreter(sid, verrou=True):
    def faire():
        s = _SESSIONS.pop(sid, None)
        if not s:
            return
        if s["p"].poll() is None:
            s["p"].terminate()
            try:
                s["p"].wait(timeout=5)
            except subprocess.TimeoutExpired:
                s["p"].kill()
        shutil.rmtree(s["dossier"], ignore_errors=True)
    if verrou:
        with _VERROU:
            faire()
    else:
        faire()


def nettoyer():
    """Arrête les lectures abandonnées (onglet fermé, téléphone en veille…)."""
    limite = time.time() - INACTIVITE
    for sid in [k for k, s in list(_SESSIONS.items()) if s["vu"] < limite]:
        arreter(sid)


def _secondes(t):
    morceaux = t.replace(",", ".").split(":")
    return sum(float(m) * 60 ** k for k, m in enumerate(reversed(morceaux)))


def _horodatage(x):
    h, reste = divmod(max(0.0, x), 3600)
    m, sec = divmod(reste, 60)
    return f"{int(h):02d}:{int(m):02d}:{sec:06.3f}"


def decaler_vtt(vtt, debut):
    """Recale les sous-titres sur le moment où la lecture a commencé (la vidéo, elle, repart de 0)."""
    sortie, bloc = ["WEBVTT", ""], []
    for ligne in vtt.splitlines() + [""]:
        if ligne.strip():
            bloc.append(ligne)
            continue
        horaire = next((l for l in bloc if "-->" in l), None)
        if horaire:
            a, _, b = horaire.partition("-->")
            fin_cue = _secondes(b.split()[0]) - debut
            if fin_cue > 0:
                texte = bloc[bloc.index(horaire) + 1:]
                sortie += [f"{_horodatage(_secondes(a.strip()) - debut)} --> {_horodatage(fin_cue)}"] + texte + [""]
        bloc = []
    return "\n".join(sortie)


def soustitres(source, index, debut=0.0):
    """Une piste de sous-titres texte en WebVTT, calée sur le moment où la lecture a commencé."""
    with bac_a_sable.entree(source) as (entree, fds):
        cmd = (["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
               + entree + ["-map", f"0:{int(index)}", "-f", "webvtt", "-"])
        r = bac_a_sable.run(cmd, fds, capture_output=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError("Sous-titres illisibles.")
    return decaler_vtt(r.stdout.decode("utf-8", "replace"), float(debut)).encode()
