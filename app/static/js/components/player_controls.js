// Commandes du lecteur vidéo : son, plein écran, image dans l'image, et leurs icônes.
//
// Sommaire :
//   partagé : PLAYER_ICONS, setupSound, setupFullscreen, setupPictureInPicture
import { $ } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { toast } from "./dialogs.js";

export const PLAYER_ICONS = {
  jouer: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z" fill="currentColor"/></svg>',
  pause:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" fill="currentColor"/></svg>',
  son: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" fill="currentColor"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18 6a8.5 8.5 0 0 1 0 12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  muet: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" fill="currentColor"/><path d="M15.5 9.5l5 5M20.5 9.5l-5 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  reglages:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8.6a3.4 3.4 0 1 0 0 6.8 3.4 3.4 0 0 0 0-6.8zm8.1 4.6.1-1.2-.1-1.2 2-1.6-2-3.4-2.4 1a8 8 0 0 0-2-1.2L15.3 3h-4l-.4 2.6a8 8 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6-.1 1.2.1 1.2-2 1.6 2 3.4 2.4-1a8 8 0 0 0 2 1.2l.4 2.6h4l.4-2.6a8 8 0 0 0 2-1.2l2.4 1 2-3.4z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  incrustation:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><rect x="12" y="11" width="7" height="6" rx="1" fill="currentColor"/></svg>',
  plein:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  sortir:
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4v5H4M20 9h-5V4M15 20v-5h5M4 15h5v5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

// Son : bouton muet, réglette de volume (gardée d'une vidéo à l'autre).
export function setupSound(video) {
  const soundButton = $("#lect-son");
  const volume = $("#lect-volume");
  const updateSound = () => {
    soundButton.innerHTML = video.muted || video.volume === 0 ? PLAYER_ICONS.muet : PLAYER_ICONS.son;
    soundButton.setAttribute("aria-label", video.muted ? t("Remettre le son") : t("Couper le son"));
    volume.value = String(video.muted ? 0 : video.volume);
  };
  try {
    const value = parseFloat(localStorage.getItem("jabba-volume"));
    if (value >= 0 && value <= 1) video.volume = value;
  } catch (_) {
    /* rien */
  }
  soundButton.onclick = () => {
    video.muted = !video.muted;
    if (!video.muted && video.volume === 0) video.volume = 0.5;
    updateSound();
  };
  volume.addEventListener("input", () => {
    video.volume = +volume.value;
    video.muted = +volume.value === 0;
    try {
      localStorage.setItem("jabba-volume", volume.value);
    } catch (_) {
      /* rien */
    }
    updateSound();
  });
  video.addEventListener("volumechange", updateSound);
  updateSound();
}

// Renvoie isFullscreen(), dont l'image dans l'image a besoin.
export function setupFullscreen(area, wakeControls) {
  // Plein écran avec NOS commandes. Sur iPhone, le plein écran du système reprendrait la vidéo
  // dans son propre lecteur (marqué « direct », barre fausse) : on remplit l'écran nous-mêmes.
  const isFullscreen = () =>
    document.fullscreenElement === area ||
    document.webkitFullscreenElement === area ||
    area.classList.contains("plein");
  $("#lect-plein").onclick = () => {
    if (document.fullscreenElement || document.webkitFullscreenElement)
      (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    else if (area.classList.contains("plein")) area.classList.remove("plein");
    else if (area.requestFullscreen) area.requestFullscreen().catch(() => area.classList.add("plein"));
    else if (area.webkitRequestFullscreen) area.webkitRequestFullscreen();
    else area.classList.add("plein");
    wakeControls();
  };
  const updateFullscreen = () => {
    const fullscreen = isFullscreen();
    $("#lect-plein").innerHTML = fullscreen ? PLAYER_ICONS.sortir : PLAYER_ICONS.plein;
    $("#lect-plein").setAttribute("aria-label", fullscreen ? t("Quitter le plein écran") : t("Plein écran"));
  };
  document.addEventListener("fullscreenchange", updateFullscreen);
  document.addEventListener("webkitfullscreenchange", updateFullscreen);
  new MutationObserver(updateFullscreen).observe(area, { attributes: true, attributeFilter: ["class"] });
  return isFullscreen;
}

export function setupPictureInPicture(video, isFullscreen) {
  // Image dans l'image : la vidéo continue dans une petite fenêtre flottante (autre appli, autre fenêtre).
  // Coupé par défaut (sinon l'iPhone pourrait la sortir tout seul de nos commandes) ; activé sur demande.
  const pipButton = $("#lect-incrustation");
  const pipSupported =
    (document.pictureInPictureEnabled && video.requestPictureInPicture) ||
    typeof video.webkitSetPresentationMode === "function";
  if (pipSupported) {
    pipButton.hidden = false;
    pipButton.onclick = async () => {
      try {
        if (document.pictureInPictureElement === video) await document.exitPictureInPicture();
        else if (video.webkitPresentationMode === "picture-in-picture") video.webkitSetPresentationMode("inline");
        else {
          video.disablePictureInPicture = false;
          if (isFullscreen()) $("#lect-plein").onclick();
          if (video.requestPictureInPicture && document.pictureInPictureEnabled) await video.requestPictureInPicture();
          else video.webkitSetPresentationMode("picture-in-picture");
          if (video.paused) video.play().catch(() => {});
        }
      } catch (_) {
        toast(t("Image dans l'image impossible pour cette vidéo."), true);
      }
    };
    const leavePip = () => {
      video.disablePictureInPicture = true;
    };
    video.addEventListener("leavepictureinpicture", leavePip);
    video.addEventListener("webkitpresentationmodechanged", () => {
      if (video.webkitPresentationMode === "inline") leavePip();
    });
  }
}
