// Entoure chaque texte affiché de l'interface par t(…) ou t`…` (voir app/static/js/core/i18n.js).
//   node baliser_textes.cjs app/static/js            → balise ce qui manque, puis lancer extraire_textes.cjs
//   node baliser_textes.cjs app/static/js --verifier → sort en erreur s'il reste un texte non balisé (tests)
// Un texte = des mots (espaces, accents, ou un mot en majuscule) ; les sélecteurs, chemins d'API,
// comparaisons et noms d'attributs sont laissés tels quels.
const acorn = require("acorn");
const fs = require("fs");
const path = require("path");

const SKIP_CALLS = new Set([
  "querySelector", "querySelectorAll", "$", "getAttribute", "setAttribute", "removeAttribute", "hasAttribute",
  "addEventListener", "removeEventListener", "add", "remove", "toggle", "contains", "getItem", "setItem", "removeItem",
  "startsWith", "endsWith", "includes", "indexOf", "split", "join", "matches", "closest", "createElement", "open",
  "api", "fetch", "replace", "match", "test", "getElementById", "dispatchEvent", "postMessage", "emulate", "send",
  "setData", "getData", "RegExp", "Event", "CustomEvent", "registerServiceWorker", "register", "canPlayType",
  "toLocaleString", "toLocaleDateString", "toLocaleTimeString", "Intl", "Collator", "DateTimeFormat", "NumberFormat",
  "isTypeSupported", "insertAdjacentHTML", "createObjectURL", "encodeURIComponent", "decodeURIComponent",
]);
const SETATTR_TEXT = new Set(["aria-label", "title", "placeholder", "alt"]);

function visibleText(s) {
  return s.replace(/<[^>]*>/g, " ").replace(/&[a-z]+;/g, " ");
}
function looksLikeText(raw) {
  if (/^\s*$/.test(raw)) return false;
  if (/^[/#.?&]/.test(raw.trim()) && !/<|\s[a-zà-ÿ]{3,}\s/.test(raw)) return false;
  if (/^[a-z-]+\[|\[name=|\[data-|^dialog|^input|^button\b|^\.[a-z]/.test(raw)) return false;
  if (/^(application|text|image|video|audio)\/[\w.+-]+$/.test(raw)) return false;
  const text = visibleText(raw);
  if (/[a-zà-ÿ]{2,}[ '’][a-zà-ÿ]{2,}/i.test(text) && /[a-zà-ÿ]{3,}/i.test(text)) return true;
  if (/[àâéèêëîïôûùçœÀÂÉÈÊÎÔÛÇ]/.test(text) && /[a-zà-ÿ]{3,}/i.test(text)) return true;
  if (/(^|[\s>])[A-ZÀ-Ý][a-zà-ÿ'’]{2,}[.!?…:]?(\s|$|<)/.test(text) && !/^[A-Z][a-z]+[A-Z]/.test(text.trim())) return true;
  return false;
}

function calleeName(call) {
  const c = call.callee;
  if (c.type === "Identifier") return c.name;
  if (c.type === "MemberExpression" && !c.computed) return c.property.name;
  return "";
}

function tagFile(file, dry) {
  if (file.endsWith("core/i18n.js")) return 0;
  const src = fs.readFileSync(file, "utf8");
  const ast = acorn.parse(src, { ecmaVersion: "latest", sourceType: "module" });
  const inserts = [];
  function visit(node, parent, key) {
    if (!node || typeof node.type !== "string") return;
    if (node.type === "TaggedTemplateExpression") {
      // déjà étiqueté (t`…`) : on ne touche qu'aux expressions dedans
      node.quasi.expressions.forEach((e) => visit(e, node.quasi, "expressions"));
      return;
    }
    if (node.type === "ImportDeclaration" || node.type === "ExportAllDeclaration") return;
    if (node.type === "Literal" && typeof node.value === "string") {
      if (!looksLikeText(node.value)) return;
      if (parent.type === "Property" && key === "key") return;
      if (parent.type === "MemberExpression" && key === "property") return;
      if (parent.type === "BinaryExpression" || parent.type === "SwitchCase" || parent.type === "ExportNamedDeclaration") return;
      if (parent.type === "CallExpression" || parent.type === "NewExpression") {
        const name = calleeName(parent);
        if (name === "t") return;
        if (name === "setAttribute") {
          const attr = parent.arguments[0];
          if (!(attr && attr.type === "Literal" && SETATTR_TEXT.has(attr.value) && parent.arguments[1] === node)) return;
        } else if (SKIP_CALLS.has(name)) return;
      }
      inserts.push([node.end, ")"], [node.start, "t("]);
      return;
    }
    if (node.type === "TemplateLiteral") {
      const text = node.quasis.map((q) => q.value.cooked).join("{}");
      const skipCall =
        (parent.type === "CallExpression" || parent.type === "NewExpression") &&
        SKIP_CALLS.has(calleeName(parent)) &&
        calleeName(parent) !== "insertAdjacentHTML";
      if (looksLikeText(text) && !skipCall && parent.type !== "BinaryExpression") inserts.push([node.start, "t"]);
      node.expressions.forEach((e) => visit(e, node, "expressions"));
      return;
    }
    for (const k of Object.keys(node)) {
      const v = node[k];
      if (Array.isArray(v)) v.forEach((child) => visit(child, node, k));
      else if (v && typeof v.type === "string") visit(v, node, k);
    }
  }
  visit(ast, { type: "Root" }, "");
  if (!inserts.length) return 0;
  inserts.sort((a, b) => b[0] - a[0]);
  let out = src;
  for (const [pos, text] of inserts) out = out.slice(0, pos) + text + out.slice(pos);
  const relative = path.relative(path.dirname(file), path.join(ROOT, "core", "i18n.js")).replace(/\\/g, "/");
  const importLine = `import { t } from "${relative.startsWith(".") ? relative : "./" + relative}";\n`;
  if (!/import \{ t \} from/.test(out)) {
    const lastImport = [...out.matchAll(/^import [^;]+;\n/gm)].pop();
    const at = lastImport ? lastImport.index + lastImport[0].length : out.indexOf("\n", out.search(/^[^/]/m)) + 1;
    out = out.slice(0, at) + importLine + out.slice(at);
  }
  if (!dry) fs.writeFileSync(file, out);
  return inserts.filter(([, x]) => x !== ")").length;
}

const ROOT = path.resolve(process.argv[2]);
const check = process.argv.includes("--verifier");
const dry = check || process.argv.includes("--essai");
let total = 0;
(function walk(d) {
  for (const f of fs.readdirSync(d)) {
    const p = path.join(d, f);
    if (fs.statSync(p).isDirectory()) {
      if (f !== "vendor") walk(p);
    } else if (p.endsWith(".js")) {
      const n = tagFile(p, dry);
      total += n;
      if (n) console.log(String(n).padStart(4), path.relative(ROOT, p));
    }
  }
})(ROOT);
console.log("total", total);
if (check && total) {
  console.error("Des textes ne passent pas par t() : lance node tests/outils_js/baliser_textes.cjs app/static/js");
  process.exit(1);
}
