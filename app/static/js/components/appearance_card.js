// Carte « Apparence » de l'écran Système : le style (Cube, Salle des machines, Atelier clair) et le thème
// (clair, sombre, ou comme l'appareil).
//
// Sommaire :
//   partagé : appearanceCard, applyTheme, applyStyle (et le choix de la langue, dès qu'une traduction existe)
//   interne : THEMES, STYLES, THEME_COLORS, savedTheme, savedStyle, saved, updateBarColor
//
// Le choix vaut pour cet appareil (gardé dans le navigateur) : le Mac peut rester en clair
// pendant que l'iPhone suit son propre réglage.
import { $ } from "../core/dom.js";
import { chooseLanguage, LANGUAGE, LANGUAGES, t } from "../core/i18n.js";

const THEMES = [
  ["auto", t("Comme l'appareil")],
  ["clair", t("Clair")],
  ["sombre", t("Sombre")],
];
// Les styles : [valeur, nom, ce qu'il évoque]. Feuilles style_*.css ; « cube » est celui de base.
const STYLES = [
  ["cube", t("Cube"), t("Vert d'eau, formes rondes")],
  ["machines", t("Salle des machines"), t("Sombre et chaud, voyants ambre")],
  ["atelier", t("Atelier clair"), t("Papier, traits d'encre, orange")],
];
// Couleur de la barre d'état de l'iPhone : celle du fond, pour chaque style en clair et en sombre.
const THEME_COLORS = {
  cube: { clair: "#EEF4F2", sombre: "#0B1514" },
  machines: { clair: "#F4EFE6", sombre: "#17140F" },
  atelier: { clair: "#F3F1EC", sombre: "#151412" },
};

function saved(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch (_) {
    return fallback;
  }
}

function savedTheme() {
  return saved("jabba-theme", "auto");
}

function savedStyle() {
  return saved("jabba-style", "cube");
}

function updateBarColor() {
  const theme = savedTheme();
  const colors = THEME_COLORS[savedStyle()] || THEME_COLORS.cube;
  document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => {
    const followsDevice = meta.media.includes("dark") ? "sombre" : "clair";
    meta.content = colors[theme === "auto" ? followsDevice : theme];
  });
}

export function applyStyle(style) {
  const root = document.documentElement;
  if (style === "machines" || style === "atelier") root.dataset.style = style;
  else delete root.dataset.style;
  try {
    if (style === "cube") localStorage.removeItem("jabba-style");
    else localStorage.setItem("jabba-style", style);
  } catch (_) {
    /* navigation privée : le choix vaut pour cette visite */
  }
  updateBarColor();
}

export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "clair" || theme === "sombre") root.dataset.theme = theme;
  else delete root.dataset.theme;
  try {
    if (theme === "auto") localStorage.removeItem("jabba-theme");
    else localStorage.setItem("jabba-theme", theme);
  } catch (_) {
    /* navigation privée : le choix vaut pour cette visite */
  }
  updateBarColor();
}

export function appearanceCard() {
  const element = $("#apparence");
  if (!element) return;
  const current = savedTheme();
  const currentStyle = savedStyle();
  element.innerHTML = t`<h2>Apparence</h2>
    <p class="note" style="margin:6px 0 12px">Pour cet appareil seulement.</p>
    <div class="choix-style" role="radiogroup" aria-label="Style">${STYLES.map(
      ([value, label, hint]) =>
        `<label class="option-style" data-apercu="${value}"><input type="radio" name="style" value="${value}"${value === currentStyle ? " checked" : ""}><span class="pastilles-style" aria-hidden="true"><i></i><i></i><i></i></span><span><strong>${label}</strong><br><span class="note">${hint}</span></span></label>`,
    ).join("")}</div>
    <div class="choix-theme" role="radiogroup" aria-label="Thème">${THEMES.map(
      ([value, label]) =>
        `<label class="option-theme"><input type="radio" name="theme" value="${value}"${value === current ? " checked" : ""}><span>${label}</span></label>`,
    ).join("")}</div>`;
  element.querySelectorAll('input[name="theme"]').forEach((input) => {
    input.onchange = () => applyTheme(input.value);
  });
  element.querySelectorAll('input[name="style"]').forEach((input) => {
    input.onchange = () => applyStyle(input.value);
  });
  // Choix de la langue : seulement quand une traduction existe (voir core/i18n.js).
  if (Object.keys(LANGUAGES).length > 1) {
    element.insertAdjacentHTML(
      "beforeend",
      `<label class="ligne-reglage" style="margin-top:12px"><span>${t("Langue")}</span><select id="langue">${Object.entries(
        LANGUAGES,
      )
        .map(([code, name]) => `<option value="${code}"${code === LANGUAGE ? " selected" : ""}>${name}</option>`)
        .join("")}</select></label>`,
    );
    $("#langue").onchange = (event) => chooseLanguage(event.target.value);
  }
}
