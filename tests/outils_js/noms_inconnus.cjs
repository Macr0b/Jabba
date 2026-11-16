// Noms utilisés mais jamais déclarés ni importés, dans chaque module de l'interface (sinon : erreur à l'exécution).
const acorn = require("acorn");
const eslintScope = require("eslint-scope");
const fs = require("fs");
const path = require("path");
const GLOBALS = new Set(Object.getOwnPropertyNames(globalThis).concat([
  "window", "document", "navigator", "location", "localStorage", "sessionStorage", "fetch", "FormData", "URL", "URLSearchParams",
  "Blob", "File", "FileReader", "Headers", "Request", "Response", "AbortController", "Notification", "WebSocket", "Image",
  "HTMLElement", "HTMLVideoElement", "Event", "CustomEvent", "KeyboardEvent", "MutationObserver", "ResizeObserver", "IntersectionObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle", "matchMedia", "history", "screen", "alert", "confirm",
  "self", "caches", "clients", "registration", "Hls", "atob", "btoa", "crypto", "performance",
  "MediaSource", "PushManager", "ServiceWorkerRegistration", "DOMParser", "XMLHttpRequest", "Node", "NodeFilter", "Range",
  "CSS", "DataTransfer", "ClipboardItem", "visualViewport", "innerWidth", "innerHeight", "scrollTo", "open", "close", "print",
]));
let problems = 0;
for (const file of process.argv.slice(2)) {
  const src = fs.readFileSync(file, "utf8");
  const ast = acorn.parse(src, { ecmaVersion: "latest", sourceType: "module", locations: true, ranges: true });
  const scopes = eslintScope.analyze(ast, { ecmaVersion: 2022, sourceType: "module" });
  for (const ref of scopes.globalScope.through) {
    const name = ref.identifier.name;
    if (!GLOBALS.has(name)) {
      problems++;
      console.log(`${path.relative(process.cwd(), file)}:${ref.identifier.loc.start.line} ${name}`);
    }
  }
}
process.exit(problems ? 1 : 0);
