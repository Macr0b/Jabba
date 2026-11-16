// Envois de fichiers avec une barre d'avancement par fichier, et « Réessayer » sur celui qui a échoué.
//
// Sommaire :
//   partagé : uploadFiles
//   interne : panel, renderItem, sendOne, next, finishIfIdle
//
// Chaque fichier part seul (2 à la fois) : un échec ne fait pas renvoyer les autres.
// Le panneau reste en bas à droite tant qu'un envoi est en cours ou a échoué.
import { escapeHtml } from "../core/dom.js";
import { formatBytes } from "../core/format.js";
import { t } from "../core/i18n.js";

const PARALLEL_UPLOADS = 2;
const queue = []; // {id, file, path, state: attente|envoi|fini|erreur, sent, message, done}
let running = 0;
let nextId = 1;
let panelElement = null;

function panel() {
  if (panelElement && document.body.contains(panelElement)) return panelElement;
  panelElement = document.createElement("section");
  panelElement.className = "envois";
  panelElement.setAttribute("aria-label", t("Envois en cours"));
  panelElement.setAttribute("aria-live", "polite");
  document.body.appendChild(panelElement);
  return panelElement;
}

function renderItem(item) {
  const percent = item.file.size ? Math.round((item.sent / item.file.size) * 100) : 100;
  const status =
    item.state === "fini"
      ? '<span class="envoi-etat ok">✓</span>'
      : item.state === "erreur"
        ? t`<button class="btn btn-petit" type="button" data-reessayer="${item.id}">Réessayer</button>`
        : `<span class="envoi-etat">${item.state === "attente" ? t("En attente") : percent + " %"}</span>`;
  return t`<li class="envoi" data-etat="${item.state}">
      <span class="envoi-nom">${escapeHtml(item.file.name)}<span class="note"> · ${formatBytes(item.file.size)}</span>
        ${item.message ? `<span class="envoi-erreur">${escapeHtml(item.message)}</span>` : ""}</span>
      ${status}
      <progress max="100" value="${item.state === "fini" ? 100 : percent}" aria-label="Avancement de ${escapeHtml(item.file.name)}"></progress>
    </li>`;
}

function render() {
  const visible = queue.filter((item) => item.state !== "masque");
  if (!visible.length) {
    if (panelElement) panelElement.remove();
    panelElement = null;
    return;
  }
  const finished = visible.filter((item) => item.state === "fini").length;
  const failed = visible.filter((item) => item.state === "erreur").length;
  const element = panel();
  element.innerHTML = t`<div class="envois-titre"><strong>${finished} / ${visible.length} envoyé${finished > 1 ? "s" : ""}</strong>
      ${failed ? t`<span class="note">${failed} en erreur</span>` : ""}
      <button class="btn btn-petit" type="button" id="fermer-envois"${running || queue.some((item) => item.state === "attente") ? " disabled" : ""}>Fermer</button></div>
    <ul>${visible.map(renderItem).join("")}</ul>`;
  element.querySelector("#fermer-envois").onclick = () => {
    queue.forEach((item) => (item.state = "masque"));
    render();
  };
  element.querySelectorAll("[data-reessayer]").forEach((button) => {
    button.onclick = () => {
      const item = queue.find((entry) => entry.id === +button.dataset.reessayer);
      Object.assign(item, { state: "attente", sent: 0, message: "" });
      next();
    };
  });
}

function sendOne(item) {
  return new Promise((resolve) => {
    const request = new XMLHttpRequest();
    const formData = new FormData();
    formData.append("fichiers", item.file, item.file.name);
    request.open("POST", "/api/fichiers/envoyer?chemin=" + encodeURIComponent(item.path));
    request.setRequestHeader("X-Jabba", "1");
    request.upload.onprogress = (event) => {
      item.sent = event.loaded;
      render();
    };
    request.onload = () => {
      let message = "";
      try {
        const body = JSON.parse(request.responseText);
        const detail = body.detail || body.erreur;
        message = (detail && (detail.message || (typeof detail === "string" ? detail : ""))) || "";
      } catch (_) {
        /* réponse vide */
      }
      if (request.status >= 200 && request.status < 300) item.state = "fini";
      else {
        item.state = "erreur";
        item.message =
          request.status === 401 ? t("Reconnecte-toi, puis réessaie.") : message || t`Erreur ${request.status}`;
      }
      resolve();
    };
    request.onerror = () => {
      item.state = "erreur";
      item.message = t("Le serveur ne répond pas.");
      resolve();
    };
    request.send(formData);
  });
}

async function next() {
  render();
  while (running < PARALLEL_UPLOADS) {
    const item = queue.find((entry) => entry.state === "attente");
    if (!item) break;
    item.state = "envoi";
    running++;
    sendOne(item).then(() => {
      running--;
      if (item.state === "fini" && item.done) item.done();
      next();
    });
  }
  render();
  finishIfIdle();
}

// Quand tout est envoyé sans erreur, le panneau se retire tout seul.
function finishIfIdle() {
  if (running || queue.some((item) => item.state === "attente" || item.state === "erreur")) return;
  setTimeout(() => {
    if (running || queue.some((item) => item.state === "attente" || item.state === "erreur")) return;
    queue.forEach((item) => (item.state = "masque"));
    render();
  }, 2500);
}

// Envoie les fichiers dans le dossier `path`. `afterEach` est appelé après chaque fichier arrivé.
export function uploadFiles(path, files, afterEach = () => {}) {
  const kept = [...files].filter((file) => file.size || file.type);
  if (!kept.length) return false;
  let refreshTimer = null;
  const done = () => {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(afterEach, 400); // un seul rafraîchissement pour une rafale de fichiers
  };
  kept.forEach((file) => queue.push({ id: nextId++, file, path, state: "attente", sent: 0, message: "", done }));
  next();
  return true;
}
