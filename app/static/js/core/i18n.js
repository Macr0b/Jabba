// Traduction des textes de l'interface.
//
// Sommaire :
//   partagé : t, LANGUAGE, LANGUAGES, chooseLanguage
//   interne : catalog, load, fill
//
// Principe (comme gettext) : le texte français reste dans le code, et sert de clé.
//   t`Bonjour ${nom}`   → cherche « Bonjour {0} » dans le catalogue de la langue choisie
//   t("Annuler")        → cherche « Annuler »
// Tous les textes sont rassemblés dans static/locales/fr.json (regénéré par
// tests/outils_js/extraire_textes.cjs, vérifié par les tests). Pour traduire : copier fr.json
// en en.json (par exemple), remplacer chaque valeur, et ajouter la langue à LANGUAGES.
// Un texte absent du catalogue s'affiche en français : une traduction partielle ne casse rien.

export const LANGUAGES = { fr: "Français" };
const DEFAULT_LANGUAGE = "fr";

function savedLanguage() {
  try {
    const saved = localStorage.getItem("jabba-langue");
    if (saved && LANGUAGES[saved]) return saved;
  } catch (_) {
    /* navigation privée */
  }
  const fromDevice = (navigator.language || "").slice(0, 2);
  return LANGUAGES[fromDevice] ? fromDevice : DEFAULT_LANGUAGE;
}

export const LANGUAGE = savedLanguage();
let catalog = {};

async function load(language) {
  if (language === DEFAULT_LANGUAGE) return {}; // le français est dans le code : rien à charger
  try {
    const response = await fetch(`/static/locales/${language}.json`);
    return response.ok ? await response.json() : {};
  } catch (_) {
    return {};
  }
}

// Attente au chargement du module : chaque écran qui importe t() a son catalogue prêt.
catalog = await load(LANGUAGE);
document.documentElement.lang = LANGUAGE;

// Les retours à la ligne et l'indentation du code ne font pas partie du texte à traduire.
const normalize = (text) => text.replace(/\s+/g, " ").trim();

function fill(text, values) {
  return text.replace(/\{(\d+)\}/g, (match, index) => (index < values.length ? String(values[index]) : match));
}

// Utilisable en gabarit étiqueté (t`…${x}…`) ou en appel simple (t("…")).
export function t(strings, ...values) {
  if (typeof strings === "string") return catalog[normalize(strings)] ?? strings;
  const key = normalize(strings.reduce((all, part, i) => all + `{${i - 1}}` + part));
  const translated = catalog[key];
  if (translated === undefined) return strings.reduce((all, part, i) => all + values[i - 1] + part);
  return fill(translated, values);
}

export function chooseLanguage(language) {
  try {
    localStorage.setItem("jabba-langue", language);
  } catch (_) {
    /* navigation privée */
  }
  location.reload();
}
