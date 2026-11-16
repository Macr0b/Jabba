// Carte « Apparence » de l'écran Système : clair, sombre, ou comme l'appareil.
//
// Sommaire :
//   partagé : appearanceCard, applyTheme
//   interne : THEMES, THEME_COLORS, savedTheme
//
// Le choix vaut pour cet appareil (gardé dans le navigateur) : le Mac peut rester en clair
// pendant que l'iPhone suit son propre réglage.
import { $ } from "../core/dom.js";

const THEMES = [
  ["auto", "Comme l'appareil"],
  ["clair", "Clair"],
  ["sombre", "Sombre"],
];
const THEME_COLORS = { clair: "#F4F5F7", sombre: "#121418" };

function savedTheme() {
  try {
    return localStorage.getItem("jabba-theme") || "auto";
  } catch (_) {
    return "auto";
  }
}

export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "clair" || theme === "sombre") root.dataset.theme = theme;
  else delete root.dataset.theme;
  // Barre d'état de l'iPhone : de la couleur du thème choisi, ou selon l'appareil en automatique.
  document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => {
    const followsDevice = meta.media.includes("dark") ? "sombre" : "clair";
    meta.content = THEME_COLORS[theme === "auto" ? followsDevice : theme];
  });
  try {
    if (theme === "auto") localStorage.removeItem("jabba-theme");
    else localStorage.setItem("jabba-theme", theme);
  } catch (_) {
    /* navigation privée : le choix vaut pour cette visite */
  }
}

export function appearanceCard() {
  const element = $("#apparence");
  if (!element) return;
  const current = savedTheme();
  element.innerHTML = `<h2>Apparence</h2>
    <p class="note" style="margin:6px 0 12px">Pour cet appareil seulement.</p>
    <div class="choix-theme" role="radiogroup" aria-label="Thème">${THEMES.map(
      ([value, label]) =>
        `<label class="option-theme"><input type="radio" name="theme" value="${value}"${value === current ? " checked" : ""}><span>${label}</span></label>`,
    ).join("")}</div>`;
  element.querySelectorAll('input[name="theme"]').forEach((input) => {
    input.onchange = () => applyTheme(input.value);
  });
}
