// Rassemble tous les textes de l'interface (t("…") et t`…`) dans app/static/locales/fr.json.
//   node extraire_textes.cjs            → réécrit fr.json
//   node extraire_textes.cjs --verifier → sort en erreur si fr.json n'est plus à jour (tests)
// Les clés sont le texte français, les « ${…} » devenant {0}, {1}…, espaces et retours à la ligne
// réduits à un seul espace (voir app/static/js/core/i18n.js).
const acorn = require("acorn");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..", "app", "static");
const TARGET = path.join(ROOT, "locales", "fr.json");

const normalize = (text) => text.replace(/\s+/g, " ").trim(); // comme dans core/i18n.js

function keysOf(file) {
  const ast = acorn.parse(fs.readFileSync(file, "utf8"), { ecmaVersion: "latest", sourceType: "module" });
  const keys = [];
  (function visit(node) {
    if (!node || typeof node.type !== "string") return;
    if (node.type === "TaggedTemplateExpression" && node.tag.type === "Identifier" && node.tag.name === "t") {
      const quasis = node.quasi.quasis.map((q) => q.value.cooked);
      keys.push(normalize(quasis.reduce((all, part, i) => all + `{${i - 1}}` + part)));
    }
    if (node.type === "CallExpression" && node.callee.type === "Identifier" && node.callee.name === "t") {
      const first = node.arguments[0];
      if (first && first.type === "Literal" && typeof first.value === "string") keys.push(normalize(first.value));
    }
    for (const key of Object.keys(node)) {
      const value = node[key];
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value.type === "string") visit(value);
    }
  })(ast);
  return keys;
}

const all = new Set();
(function walk(dir) {
  for (const name of fs.readdirSync(dir).sort()) {
    const full = path.join(dir, name);
    if (fs.statSync(full).isDirectory()) {
      if (name !== "vendor") walk(full);
    } else if (name.endsWith(".js")) keysOf(full).forEach((key) => all.add(key));
  }
})(path.join(ROOT, "js"));

const catalog = {};
[...all].sort((a, b) => a.localeCompare(b, "fr")).forEach((key) => (catalog[key] = key));
const text = JSON.stringify(catalog, null, 2) + "\n";
if (process.argv.includes("--verifier")) {
  const current = fs.existsSync(TARGET) ? fs.readFileSync(TARGET, "utf8") : "";
  if (current !== text) {
    console.error("app/static/locales/fr.json n'est plus à jour : lance node tests/outils_js/extraire_textes.cjs");
    process.exit(1);
  }
  console.log(`${all.size} textes, catalogue à jour`);
} else {
  fs.mkdirSync(path.dirname(TARGET), { recursive: true });
  fs.writeFileSync(TARGET, text);
  console.log(`${all.size} textes écrits dans ${path.relative(process.cwd(), TARGET)}`);
}
