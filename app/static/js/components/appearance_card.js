// Carte « Apparence » de l'écran Système : clair, sombre, ou comme l'appareil.
//
// Sommaire :
//   partagé : appearanceCard, applyTheme (et le choix de la langue, dès qu'une traduction existe)
//   interne : THEMES, THEME_COLORS, savedTheme
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
const THEME_COLORS = { clair: "#EEF4F2", sombre: "#0B1514" };

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
  element.innerHTML = t`<h2>Apparence</h2>
    <p class="note" style="margin:6px 0 12px">Pour cet appareil seulement.</p>
    <div class="choix-theme" role="radiogroup" aria-label="Thème">${THEMES.map(
      ([value, label]) =>
        `<label class="option-theme"><input type="radio" name="theme" value="${value}"${value === current ? " checked" : ""}><span>${label}</span></label>`,
    ).join("")}</div>`;
  element.querySelectorAll('input[name="theme"]').forEach((input) => {
    input.onchange = () => applyTheme(input.value);
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
