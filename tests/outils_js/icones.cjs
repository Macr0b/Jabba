// Fabrique app/static/vendor/icons.js : les icônes Tabler (licence MIT) dont l'interface se sert, et elles seules.
// À relancer après avoir ajouté un nom à LISTE : node tests/outils_js/icones.cjs
// (--verifier : échoue si le fichier n'est pas à jour, sans l'écrire).
//
// Sommaire : LISTE, lecture des fichiers SVG, écriture du module.
const fs = require("fs");
const path = require("path");

const LISTE = [
  // navigation
  "home",
  "tools",
  "folder",
  "database",
  "wifi",
  "users",
  "settings",
  "help-circle",
  "dots",
  // outils
  "cloud",
  "movie",
  "stack-2",
  "shield-check",
  "lock",
  "photo",
  "history",
  "file-text",
  "box",
  "eye",
  "tool",
  "virus",
  "world",
  "world-www",
  "device-desktop",
  "key",
  "link",
  "server",
];

const DOSSIER = path.join(__dirname, "node_modules/@tabler/icons/icons/outline");
const SORTIE = path.join(__dirname, "../../app/static/vendor/icons.js");
const version = require(path.join(__dirname, "node_modules/@tabler/icons/package.json")).version;

const icones = {};
for (const nom of LISTE) {
  const svg = fs.readFileSync(path.join(DOSSIER, nom + ".svg"), "utf8");
  // Seuls les tracés : le cadre <svg> (taille, trait) est posé par core/icons.js.
  const traces = svg
    .replace(/^[\s\S]*?<svg[^>]*>/, "")
    .replace(/<\/svg>[\s\S]*$/, "")
    .replace(/<path stroke="none" d="M0 0h24v24H0z" fill="none"\s*\/>/, "")
    .replace(/\s+/g, " ")
    .trim();
  icones[nom] = traces;
}

const contenu =
  `// Icônes Tabler ${version} (https://tabler.io/icons), licence MIT : voir LICENSE-tabler.\n` +
  `// Fichier fabriqué par tests/outils_js/icones.cjs : ne pas modifier à la main.\n` +
  `export const ICONS = ${JSON.stringify(icones, null, 2)};\n`;

if (process.argv.includes("--verifier")) {
  const actuel = fs.existsSync(SORTIE) ? fs.readFileSync(SORTIE, "utf8") : "";
  if (actuel !== contenu) {
    console.error("app/static/vendor/icons.js n'est pas à jour : node tests/outils_js/icones.cjs");
    process.exit(1);
  }
  console.log(`${LISTE.length} icônes, à jour`);
} else {
  fs.writeFileSync(SORTIE, contenu);
  console.log(`${LISTE.length} icônes écrites dans app/static/vendor/icons.js`);
}
