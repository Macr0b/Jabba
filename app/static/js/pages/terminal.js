// Écran Terminal : le terminal du serveur (super admin).
//
// Sommaire :
//   partagé : pages.terminal (écran)
//   interne : openTerminal, loadXterm, TERMINAL_KEYS
import { confirmAction, toast } from "../components/dialogs.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";

// ------------------------------------------------------------ terminal du serveur
// xterm.js est livré avec l'appli (static/vendor), chargé seulement sur cette page.
let openTerminal = null; // {ws, term, fin()}
function loadXterm() {
  if (window.Terminal && window.FitAddon) return Promise.resolve();
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "/static/vendor/xterm.css";
  document.head.appendChild(link);
  const script = (source) =>
    new Promise((ok, failed) => {
      const element = document.createElement("script");
      element.src = source;
      element.onload = ok;
      element.onerror = failed;
      document.head.appendChild(element);
    });
  return script("/static/vendor/xterm.js").then(() => script("/static/vendor/addon-fit.js"));
}

const TERMINAL_KEYS = [
  ["Échap", "\x1b"],
  ["Tab", "\t"],
  ["Ctrl+C", "\x03"],
  ["Ctrl+D", "\x04"],
  ["↑", "\x1b[A"],
  ["↓", "\x1b[B"],
  ["←", "\x1b[D"],
  ["→", "\x1b[C"],
];

pages.terminal = async () => {
  if (openTerminal) openTerminal.fin();
  content.innerHTML = `<div class="entete"><div><h1>Terminal</h1><p class="sous-titre">Le serveur, comme en SSH, avec le compte du serveur : <strong>tous les pouvoirs</strong>, réservé à toi. <code>sudo</code> redemande ton mot de passe. Les commandes tapées sont notées au Journal (à titre indicatif).</p></div>
      <div class="actions"><a class="btn" href="#/systeme">← Système</a></div></div>
    <div class="terminal-zone" id="terminal"><p class="note" style="padding:16px">Ouverture…</p></div>
    <div class="touches-terminal"><button class="btn btn-petit" type="button" id="t-coller">Coller</button><button class="btn btn-petit" type="button" id="t-copier">Copier</button>${TERMINAL_KEYS.map(([item], index) => `<button class="btn btn-petit" type="button" data-touche="${index}">${item}</button>`).join("")}</div>`;
  let ticket;
  try {
    await loadXterm();
    ticket = await api("/api/terminal/ticket", { method: "POST" });
  } catch (error) {
    $("#terminal").innerHTML = `<div class="erreur" style="margin:16px">${escapeHtml(error.message)}</div>`;
    return;
  }
  const area = $("#terminal");
  area.innerHTML = "";
  const terminal = new window.Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: "ui-monospace, Menlo, Consolas, monospace",
    scrollback: 5000,
    theme: { background: "#101318", foreground: "#E6E8EC", cursor: "#8FB3FF", selectionBackground: "#2A3A5C" },
  });
  const fitAddon = new window.FitAddon.FitAddon();
  terminal.loadAddon(fitAddon);
  terminal.open(area);
  // Le clavier de l'iPhone ne doit rien proposer (ni contacts, ni corrections, ni majuscule automatique).
  const input = area.querySelector("textarea");
  if (input) {
    for (const [key, value] of Object.entries({
      autocomplete: "off",
      autocorrect: "off",
      autocapitalize: "off",
      spellcheck: "false",
      name: "commande-" + Math.random().toString(36).slice(2),
      "data-form-type": "other",
      "aria-autocomplete": "none",
      enterkeyhint: "enter",
    }))
      input.setAttribute(key, value);
  }
  fitAddon.fit();
  const socket = new WebSocket(
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/terminal/ws?ticket=${encodeURIComponent(ticket.ticket)}`,
  );
  socket.binaryType = "arraybuffer";
  const send = (item) => {
    if (socket.readyState === 1) socket.send(JSON.stringify(item));
  };
  const size = () => {
    try {
      fitAddon.fit();
      send({ t: "r", l: terminal.rows, c: terminal.cols });
    } catch (_) {
      /* page quittée */
    }
  };
  socket.onopen = () => {
    size();
    terminal.focus();
  };
  socket.onmessage = (event) => terminal.write(new Uint8Array(event.data));
  socket.onclose = () => {
    terminal.write("\r\n\x1b[90m— Session terminée —\x1b[0m\r\n");
    if (location.hash.startsWith("#/terminal")) {
      const element = document.createElement("div");
      element.className = "actions";
      element.style.marginTop = "10px";
      element.innerHTML = '<button class="btn btn-p" type="button">Rouvrir le terminal</button>';
      element.firstChild.onclick = () => pages.terminal().catch(showError);
      area.after(element);
    }
  };
  terminal.onData((data) => send({ t: "i", d: data }));
  window.addEventListener("resize", size);
  content.querySelectorAll("[data-touche]").forEach((element) =>
    element.addEventListener("click", () => {
      send({ t: "i", d: TERMINAL_KEYS[+element.dataset.touche][1] });
      terminal.focus();
    }),
  );
  // Coller : le texte du presse-papiers part au terminal (l'iPhone demande d'abord « Coller »).
  $("#t-coller").onclick = async () => {
    let text = "";
    try {
      text = await navigator.clipboard.readText();
    } catch (_) {
      /* refusé ou non disponible */
    }
    if (!text)
      text =
        (await confirmAction({
          titre: "Coller dans le terminal",
          texte: "Colle ici (appui long, Coller), puis valide.",
          champ: "",
          valider: "Envoyer",
        })) || "";
    if (text) send({ t: "i", d: text.replace(/\r?\n/g, "\r") });
    terminal.focus();
  };
  // Copier : la sélection s'il y en a une, sinon ce qui est affiché (et ce qui précède, jusqu'à 200 lignes).
  $("#t-copier").onclick = async () => {
    let text = terminal.getSelection();
    if (!text) {
      const button = terminal.buffer.active,
        lines = [];
      for (let index = Math.max(0, button.length - 200); index < button.length; index++)
        lines.push(button.getLine(index).translateToString(true));
      text = lines.join("\n").replace(/\n+$/, "");
    }
    try {
      await navigator.clipboard.writeText(text);
      toast(terminal.hasSelection() ? "Sélection copiée" : "Écran copié (200 dernières lignes)");
    } catch (_) {
      toast("Copie impossible sur cet appareil.", true);
    }
  };
  openTerminal = {
    fin: () => {
      window.removeEventListener("resize", size);
      try {
        socket.close();
      } catch (_) {
        /* déjà fermé */
      }
      terminal.dispose();
      openTerminal = null;
    },
  };
};
window.addEventListener("hashchange", () => {
  if (openTerminal && !location.hash.startsWith("#/terminal")) openTerminal.fin();
});
