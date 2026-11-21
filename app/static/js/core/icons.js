// Les icônes de l'interface (tracés Tabler, vendor/icons.js) : une fonction qui pose le cadre SVG,
// l'icône de chaque outil et de chaque écran, et la décoration de la navigation.
//
// Sommaire :
//   partagé : icon, toolIconName, decorateNavigation
//   interne : TOOLS, SCREENS
import { ICONS } from "../../vendor/icons.js";

// L'icône de chaque outil, par identifiant (pages/home.js : toolIcon).
const TOOLS = {
  nextcloud: "cloud",
  jellyfin: "movie",
  dockge: "stack-2",
  "adguard-home": "shield-check",
  vaultwarden: "lock",
  immich: "photo",
  "time-machine": "history",
  paperless: "file-text",
  homebox: "box",
  changedetection: "eye",
  atelier: "tool",
  antivirus: "virus",
  adresses: "link",
  "jabba-app": "device-desktop",
  domaine: "world-www",
};

// L'icône de chaque écran de la navigation (attribut data-page des liens).
const SCREENS = {
  accueil: "home",
  outils: "tools",
  fichiers: "folder",
  disques: "database",
  invites: "wifi",
  comptes: "users",
  systeme: "settings",
  aide: "help-circle",
  plus: "dots",
};

/** Le SVG d'une icône, décoratif (le texte à côté dit ce qu'elle veut dire). */
export function icon(name, size = 20) {
  const paths = ICONS[name];
  if (!paths) return "";
  return `<svg class="ico" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${paths}</svg>`;
}

export function toolIconName(id) {
  return TOOLS[id] || null;
}

/** Pose l'icône de chaque écran devant son nom, dans la barre latérale et les onglets du téléphone. */
export function decorateNavigation(root = document) {
  root.querySelectorAll("nav a[data-page]").forEach((link) => {
    if (link.querySelector(".ico") || !SCREENS[link.dataset.page]) return;
    link.insertAdjacentHTML("afterbegin", icon(SCREENS[link.dataset.page]));
  });
}
