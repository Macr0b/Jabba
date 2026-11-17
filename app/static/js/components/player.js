// Lecteur vidéo : lit tous les formats (conversion par le serveur), sous-titres, son, plein écran, image dans l'image.
//
// Sommaire :
//   partagé : activePlayer, startVideo
//   interne : readableTime, loadHls, openPlayer (son, plein écran, image dans l'image : player_controls.js)
import { PLAYER_ICONS, setupFullscreen, setupPictureInPicture, setupSound } from "./player_controls.js";
import { viewUrl, viewerElement } from "./viewer.js";
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";

// ------------------------------------------------------------ lecteur vidéo
// Si le navigateur sait lire le fichier, on le lit tel quel. Sinon le serveur le
// convertit pendant la lecture (comme VLC le ferait), en HLS : Safari le lit seul,
// les autres navigateurs avec hls.js (livré avec l'appli).
export let activePlayer = null;
const readableTime = (value) => {
  value = Math.max(0, Math.floor(value || 0));
  const hours = Math.floor(value / 3600),
    minutes = Math.floor((value % 3600) / 60),
    amount = value % 60;
  return (hours ? hours + ":" + String(minutes).padStart(2, "0") : minutes) + ":" + String(amount).padStart(2, "0");
};
function loadHls() {
  if (window.Hls) return Promise.resolve();
  return new Promise((ok, failed) => {
    const script = document.createElement("script");
    script.src = "/static/vendor/hls.min.js";
    script.onload = ok;
    script.onerror = failed;
    document.head.appendChild(script);
  });
}

export async function startVideo(entry) {
  if (activePlayer) activePlayer.fin();
  let info = null;
  let serverError = "";
  try {
    info = await api("/api/lecture/infos?chemin=" + encodeURIComponent(entry.chemin));
  } catch (error) {
    serverError = error.message || ""; // moteur indisponible ou fichier illisible : on essaie la lecture directe
  }
  if (!$("#lecteur")) return;
  // Même lecteur pour toutes les vidéos : lu tel quel quand le navigateur sait le faire, converti sinon.
  return openPlayer(
    entry,
    info || { duree: 0, audios: [], soustitres: [], video: { codec: "" }, inconnu: true, erreur: serverError },
    !info || info.directe,
  );
}

// Icônes du lecteur (traits blancs, comme les lecteurs vidéo habituels).

async function openPlayer(entry, info, directPlay) {
  const area = $("#lecteur");
  if (!area) return;
  const defaultAudio = (info.audios.find((track) => track.defaut) || info.audios[0] || {}).index;
  const playback = { sid: null, debut: 0, audio: defaultAudio, sous: null, hls: null, glisse: false, fini: false };
  const texts = info.soustitres.filter((value) => value.texte);
  area.innerHTML = t`
    <video playsinline webkit-playsinline disablepictureinpicture></video>
    <div class="saut-indic gauche" id="saut-gauche" hidden></div><div class="saut-indic droite" id="saut-droite" hidden></div>
    <div class="etat-lecteur" id="etat-lecteur">${directPlay ? t("Chargement…") : t("Le serveur prépare la vidéo…")}</div>
    <div class="commandes">
      <input type="range" class="barre" id="lect-barre" min="0" max="${Math.floor(info.duree)}" value="0" step="1" aria-label="Position">
      <div class="ligne-commandes">
        <div class="groupe">
          <button class="icone-lect" type="button" id="lect-jouer" aria-label="Lecture">${PLAYER_ICONS.jouer}</button>
          <span class="volume-groupe"><button class="icone-lect" type="button" id="lect-son" aria-label="Couper le son">${PLAYER_ICONS.son}</button>
          <input type="range" id="lect-volume" class="volume" min="0" max="1" step="0.05" value="1" aria-label="Volume"></span>
          <span class="temps"><span id="lect-temps">0:00</span> / <span id="lect-duree">${readableTime(info.duree)}</span></span>
        </div>
        <div class="groupe">
          ${info.audios.length > 1 || texts.length ? t`<button class="icone-lect" type="button" id="lect-reglages" aria-label="Son et sous-titres" aria-expanded="false">${PLAYER_ICONS.reglages}</button>` : ""}
          <button class="icone-lect" type="button" id="lect-incrustation" aria-label="Image dans l'image" hidden>${PLAYER_ICONS.incrustation}</button>
          <button class="icone-lect" type="button" id="lect-plein" aria-label="Plein écran">${PLAYER_ICONS.plein}</button>
        </div>
      </div>
      <div class="menu-lect" id="lect-menu" hidden>
        ${info.audios.length > 1 ? t`<label>Son<select id="lect-audio">${info.audios.map((track) => `<option value="${track.index}"${track.index === defaultAudio ? " selected" : ""}>${escapeHtml(track.nom)}</option>`).join("")}</select></label>` : ""}
        ${texts.length ? t`<label>Sous-titres<select id="lect-sous"><option value="">Aucun</option>${texts.map((value) => `<option value="${value.index}">${escapeHtml(value.nom)}</option>`).join("")}</select></label>` : ""}
      </div>
    </div>
    <p class="note lect-info">${directPlay ? t("Lu directement") : t`Lu par le serveur (format ${escapeHtml((info.video.codec || "?").toUpperCase())})`}${info.soustitres.length > texts.length ? t(" · certains sous-titres sont des images : non affichés") : ""} · touche deux fois à gauche ou à droite de l'image : 10 s</p>`;
  const video = area.querySelector("video"),
    bar = $("#lect-barre"),
    timeLabel = $("#lect-temps"),
    playButton = $("#lect-jouer"),
    state = $("#etat-lecteur");
  const show = (message) => {
    state.textContent = message;
    state.hidden = !message;
  };

  const subtitles = () => {
    video.querySelectorAll("track").forEach((seconds) => seconds.remove());
    if (playback.sous === null) return;
    const trackElement = document.createElement("track");
    trackElement.kind = "subtitles";
    trackElement.default = true;
    trackElement.label = "Sous-titres";
    trackElement.src = `/api/lecture/soustitres?chemin=${encodeURIComponent(entry.chemin)}&piste=${playback.sous}&debut=${playback.debut}`;
    video.appendChild(trackElement);
    trackElement.addEventListener("load", () => {
      trackElement.track.mode = "showing";
    });
  };

  const from = async (seconds) => {
    if (directPlay) {
      // fichier lu tel quel : le navigateur va où on veut tout seul
      if (!playback.sid) {
        playback.sid = "direct";
        video.src = viewUrl(entry.chemin);
        subtitles();
        video.addEventListener(
          "error",
          () => {
            if (!playback.fini && !info.inconnu) {
              directPlay = false;
              playback.sid = null;
              from(seconds);
            } else
              show(
                t("Ce format ne se lit pas dans le navigateur.") +
                  (info.erreur ? " " + t`Le serveur n'a pas pu le convertir : ${info.erreur}` : ""),
              );
          },
          { once: true },
        );
        video.addEventListener(
          "loadedmetadata",
          () => {
            if (!info.duree && isFinite(video.duration)) {
              info.duree = video.duration;
              bar.max = String(Math.floor(video.duration));
              $("#lect-duree").textContent = readableTime(video.duration);
            }
            if (seconds) video.currentTime = seconds;
          },
          { once: true },
        );
        video.play().catch(() => show(t("Clique sur ▶ pour lancer la lecture.")));
      } else video.currentTime = seconds;
      return;
    }
    show(t("Le serveur prépare la vidéo…"));
    if (playback.hls) {
      playback.hls.destroy();
      playback.hls = null;
    }
    video.removeAttribute("src");
    video.load();
    let session;
    try {
      session = await api("/api/lecture/demarrer", {
        method: "POST",
        json: { chemin: entry.chemin, debut: seconds, audio: playback.audio, remplace: playback.sid },
      });
    } catch (error) {
      show(error.message);
      return;
    }
    if (playback.fini) {
      api(`/api/lecture/${session.id}/arreter`, { method: "POST" }).catch(() => {});
      return;
    }
    playback.sid = session.id;
    playback.debut = session.debut ?? seconds; // le serveur peut repartir un peu avant (image clé)…
    // … on avance alors jusqu'au moment demandé. Et les lecteurs HLS intégrés (Safari, Chrome récent)
    // partent « au plus récent » d'une liste qui grandit : on les ramène au bon endroit.
    const target = Math.max(0, seconds - playback.debut);
    video.addEventListener(
      "loadedmetadata",
      () => {
        if (Math.abs(video.currentTime - target) > 0.5) video.currentTime = target;
      },
      { once: true },
    );
    subtitles();
    // hls.js partout où il marche (iPhone récent compris) : il suit nos consignes. Le lecteur
    // intégré du navigateur, qui saute « au plus récent », ne sert qu'en dernier recours.
    try {
      await loadHls();
    } catch (_) {
      /* hors ligne : lecteur intégré */
    }
    if (!(window.Hls && window.Hls.isSupported())) {
      video.src = session.url;
      playback.garde = true;
      setTimeout(() => {
        playback.garde = false;
      }, 8000);
      video.addEventListener("timeupdate", function keep() {
        if (!playback.garde) return video.removeEventListener("timeupdate", keep);
        if (video.currentTime > target + 3) video.currentTime = target;
      });
    } else {
      // La liste grandit pendant la conversion : hls.js la prendrait pour du direct et sauterait
      // « au plus récent ». On le lui interdit : lecture depuis le début, sans rattrapage.
      playback.hls = new window.Hls({
        autoStartLoad: false,
        maxBufferLength: 40,
        manifestLoadingMaxRetry: 10,
        levelLoadingMaxRetry: 10,
        fragLoadingMaxRetry: 10,
        liveSyncDurationCount: 1e9,
        liveMaxLatencyDurationCount: Infinity,
        maxLiveSyncPlaybackRate: 1,
        liveDurationInfinity: true,
        lowLatencyMode: false,
      });
      playback.hls.on(window.Hls.Events.MANIFEST_PARSED, () => playback.hls.startLoad(0)); // toujours depuis le premier morceau
      playback.hls.on(window.Hls.Events.ERROR, (_, data) => {
        if (data.fatal) show(t("Lecture interrompue. Clique sur ▶ pour reprendre."));
      });
      playback.hls.loadSource(session.url);
      playback.hls.attachMedia(video);
    }
    video.play().catch(() => show(t("Clique sur ▶ pour lancer la lecture.")));
  };

  video.addEventListener("playing", () => show(""));
  video.addEventListener("waiting", () => show(t("Chargement…")));
  video.addEventListener("play", () => {
    playButton.innerHTML = PLAYER_ICONS.pause;
    playButton.setAttribute("aria-label", t("Pause"));
  });
  video.addEventListener("pause", () => {
    playButton.innerHTML = PLAYER_ICONS.jouer;
    playButton.setAttribute("aria-label", t("Lecture"));
  });
  video.addEventListener("timeupdate", () => {
    const seconds = playback.debut + video.currentTime;
    timeLabel.textContent = readableTime(seconds);
    if (!playback.glisse) bar.value = String(Math.floor(seconds));
    fill();
  });
  playButton.onclick = () => {
    if (!playback.sid) from(0);
    else if (video.paused) video.play();
    else video.pause();
  };
  // La barre se remplit (partie vue) comme sur les lecteurs habituels.
  const fill = () =>
    bar.style.setProperty("--pct", (info.duree ? Math.min(100, (100 * +bar.value) / info.duree) : 0) + "%");
  bar.addEventListener("input", () => {
    playback.glisse = true;
    timeLabel.textContent = readableTime(+bar.value);
    fill();
    wakeControls();
  });
  const position = () => playback.debut + video.currentTime;
  const seekTo = (seconds) => {
    seconds = Math.max(0, Math.min(seconds, (info.duree || Infinity) - 1));
    timeLabel.textContent = readableTime(seconds);
    bar.value = String(Math.floor(seconds));
    const inside = seconds - playback.debut;
    const end = video.buffered.length ? video.buffered.end(video.buffered.length - 1) : 0;
    if (directPlay || (inside >= 0 && inside <= end)) {
      playback.garde = false;
      if (directPlay) from(seconds);
      else video.currentTime = inside;
    } // déjà converti : on y va directement
    else from(seconds);
  };
  bar.addEventListener("change", () => {
    playback.glisse = false;
    seekTo(+bar.value);
  });
  // Plusieurs appuis rapides sur ±10 s s'additionnent avant de relancer (utile quand le serveur convertit).
  let jump = null;
  const shift = (data) => {
    if (!jump) jump = { cible: position() };
    jump.cible = Math.max(0, jump.cible + data);
    timeLabel.textContent = readableTime(jump.cible);
    bar.value = String(Math.floor(jump.cible));
    clearTimeout(jump.minuteur);
    jump.minuteur = setTimeout(() => {
      const direction = jump.cible;
      jump = null;
      seekTo(direction);
    }, 350);
  };
  // Comme YouTube : deux touches rapides à gauche ou à droite de l'image = −10 s / +10 s,
  // puis chaque touche suivante du même côté ajoute 10 s. Une seule touche = pause / lecture.
  const indicator = { "-1": $("#saut-gauche"), 1: $("#saut-droite") };
  let tap = { t: 0, cote: 0, serie: 0, minuteur: null, masque: null };
  const showJump = (side) => {
    const element = indicator[side];
    element.textContent = (side < 0 ? "« −" : "+") + tap.serie * 10 + " s" + (side < 0 ? "" : " »");
    element.hidden = false;
    clearTimeout(tap.masque);
    tap.masque = setTimeout(() => {
      element.hidden = true;
    }, 700);
  };
  video.onclick = null;
  video.addEventListener("click", (event) => {
    const rect = video.getBoundingClientRect(),
      value = (event.clientX - rect.left) / rect.width;
    const side = value < 0.35 ? -1 : value > 0.65 ? 1 : 0,
      now = Date.now();
    const hidden = area.classList.contains("repos");
    if (!playerMenu.hidden) {
      playerMenu.hidden = true;
      if (settingsButton) settingsButton.setAttribute("aria-expanded", "false");
      wakeControls();
      return;
    }
    wakeControls();
    if (hidden && !side) return; // commandes cachées : une touche au centre les fait juste revenir
    if (side && ((now - tap.t < 320 && tap.cote === side) || (tap.serie && tap.cote === side && now - tap.t < 700))) {
      clearTimeout(tap.minuteur);
      tap.serie = tap.serie ? tap.serie + 1 : 1;
      tap.t = now;
      tap.cote = side;
      shift(10 * side);
      showJump(side);
      return;
    }
    tap = { ...tap, t: now, cote: side, serie: 0 };
    clearTimeout(tap.minuteur);
    tap.minuteur = setTimeout(
      () => {
        if (!tap.serie) playButton.onclick();
      },
      side ? 320 : 0,
    );
  });
  video.addEventListener("dblclick", (event) => event.preventDefault());
  const keyboard = (event) => {
    if (!viewerElement.open || /INPUT|SELECT|TEXTAREA/.test(event.target.tagName)) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      shift(-10);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      shift(10);
    } else if (event.key === " " || event.key === "k") {
      event.preventDefault();
      playButton.onclick();
    } else if (event.key === "m") {
      event.preventDefault();
      $("#lect-son").onclick();
    } else if (event.key === "f") {
      event.preventDefault();
      $("#lect-plein").onclick();
    } else if (event.key === "i" && !$("#lect-incrustation").hidden) {
      event.preventDefault();
      $("#lect-incrustation").onclick();
    }
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const direction = event.key === "ArrowLeft" ? -1 : 1;
      tap.serie = tap.cote === direction && Date.now() - tap.t < 700 ? tap.serie + 1 : 1;
      tap.cote = direction;
      tap.t = Date.now();
      showJump(direction);
    }
  };
  document.addEventListener("keydown", keyboard);
  const audio = $("#lect-audio");
  if (audio)
    audio.onchange = () => {
      playback.audio = +audio.value;
      from(playback.debut + video.currentTime);
    };
  const subtitleTrack = $("#lect-sous");
  if (subtitleTrack)
    subtitleTrack.onchange = () => {
      playback.sous = subtitleTrack.value === "" ? null : +subtitleTrack.value;
      subtitles();
    };
  setupSound(video);
  const isFullscreen = setupFullscreen(area, wakeControls);
  setupPictureInPicture(video, isFullscreen);
  // Son et sous-titres : un petit menu, comme les lecteurs habituels.
  const playerMenu = $("#lect-menu"),
    settingsButton = $("#lect-reglages");
  if (settingsButton)
    settingsButton.onclick = (event) => {
      event.stopPropagation();
      playerMenu.hidden = !playerMenu.hidden;
      settingsButton.setAttribute("aria-expanded", String(!playerMenu.hidden));
      wakeControls();
    };
  playerMenu.addEventListener("click", (event) => event.stopPropagation());
  // Les commandes se cachent après 3 s sans toucher pendant la lecture ; un geste les fait revenir.
  let idleTimer = null;
  function wakeControls() {
    area.classList.remove("repos");
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      if (!video.paused && playerMenu.hidden) area.classList.add("repos");
    }, 3000);
  }
  area.addEventListener("pointermove", wakeControls);
  video.addEventListener("pause", wakeControls);
  viewerElement.addEventListener(
    "cancel",
    (event) => {
      if (area.classList.contains("plein")) {
        event.preventDefault();
        area.classList.remove("plein");
      }
    },
    { once: true },
  );
  activePlayer = {
    fin: () => {
      playback.fini = true;
      document.removeEventListener("keydown", keyboard);
      try {
        if (document.pictureInPictureElement === video) document.exitPictureInPicture();
      } catch (_) {
        /* rien */
      }
      if (playback.hls) playback.hls.destroy();
      if (playback.sid && playback.sid !== "direct")
        api(`/api/lecture/${playback.sid}/arreter`, { method: "POST" }).catch(() => {});
      video.removeAttribute("src");
      video.load();
      activePlayer = null;
    },
  };
  from(0);
}
