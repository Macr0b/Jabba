// Visionneuse : photos, PDF, textes, et le lien vers le lecteur vidéo ou Jellyfin.
//
// Sommaire :
//   partagé : THUMBNAIL_EXTENSIONS, extensionOf, kindOf, viewUrl, downloadUrl, entryIcon, viewerElement, openViewer
//   interne : EXTENSIONS, watchInJellyfin
import { toast } from "./dialogs.js";
import { startVideo, activePlayer } from "./player.js";
import { api, formBody } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";

// ------------------------------------------------------------ fichiers
const EXTENSIONS = {
  image: ["jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "svg", "tif", "tiff"],
  video: ["mp4", "m4v", "mov", "webm", "mkv"],
  audio: ["mp3", "m4a", "aac", "wav", "flac", "ogg", "opus"],
  pdf: ["pdf"],
  texte: ["txt", "md", "csv", "log", "json", "yaml", "yml", "ini", "conf", "srt", "nfo", "py", "sh"],
};
export const THUMBNAIL_EXTENSIONS = ["jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff"];
export const extensionOf = (name) => (name.includes(".") ? name.split(".").pop().toLowerCase() : "");
export const kindOf = (name) =>
  Object.keys(EXTENSIONS).find((item) => EXTENSIONS[item].includes(extensionOf(name))) || null;
export const viewUrl = (item) => "/api/fichiers/voir?chemin=" + encodeURIComponent(item);
export const downloadUrl = (item) => "/api/fichiers/telecharger?chemin=" + encodeURIComponent(item);

export function entryIcon(entry) {
  if (entry.dossier) return `<span class="ic-dossier${entry.externe ? " ext" : ""}" aria-hidden="true"></span>`;
  if (THUMBNAIL_EXTENSIONS.includes(extensionOf(entry.nom))) {
    return `<span class="vignette" aria-hidden="true"><img alt="" loading="lazy" src="/api/fichiers/apercu?chemin=${encodeURIComponent(entry.chemin)}" onerror="this.parentNode.className='ic-fichier';this.remove()"></span>`;
  }
  return `<span class="ic-fichier${kindOf(entry.nom) ? " " + kindOf(entry.nom) : ""}" aria-hidden="true"></span>`;
}

// Visionneuse : photos, vidéos, musique, PDF et textes, sans quitter l'appli.
export const viewerElement = $("#visionneuse");
export async function openViewer(list, index) {
  const entry = list[index];
  const item = kindOf(entry.nom);
  const visible = list.filter((item) => !item.dossier && kindOf(item.nom));
  const position = visible.indexOf(entry);
  let body;
  if (item === "image") body = `<img src="${viewUrl(entry.chemin)}" alt="${escapeHtml(entry.nom)}">`;
  else if (item === "video")
    body = `<div class="lecteur" id="lecteur"><p class="note">Préparation de la vidéo…</p></div>`;
  else if (item === "audio")
    body = `<div class="audio"><span class="ic-fichier audio grand" aria-hidden="true"></span><audio src="${viewUrl(entry.chemin)}" controls autoplay></audio></div>`;
  else if (item === "pdf") body = `<iframe src="${viewUrl(entry.chemin)}" title="${escapeHtml(entry.nom)}"></iframe>`;
  else body = `<pre class="texte">Chargement…</pre>`;
  viewerElement.innerHTML = `
    <div class="barre-vision">
      <button class="btn btn-petit" data-v="fermer">✕ Fermer</button>
      <strong class="nom-vision">${escapeHtml(entry.nom)}</strong>
      <span class="actions">
        ${visible.length > 1 ? `<button class="btn btn-petit" data-v="prec" aria-label="Précédent">‹</button><span class="note">${position + 1} / ${visible.length}</span><button class="btn btn-petit" data-v="suiv" aria-label="Suivant">›</button>` : ""}
        ${item === "pdf" ? `<a class="btn btn-petit" href="${viewUrl(entry.chemin)}" target="_blank" rel="noopener">Plein écran ↗</a>` : ""}
        ${item === "video" ? `<button class="btn btn-petit" data-v="jellyfin">Regarder dans Jellyfin</button>` : ""}
        <a class="btn btn-petit" href="${downloadUrl(entry.chemin)}">Télécharger</a>
      </span>
    </div>
    <div class="corps-vision">${body}</div>`;
  if (!viewerElement.open) viewerElement.showModal();
  const goTo = (data) => {
    const session = (position + data + visible.length) % visible.length;
    openViewer(list, list.indexOf(visible[session]));
  };
  viewerElement.querySelector('[data-v="fermer"]').onclick = () => viewerElement.close();
  const previousButton = viewerElement.querySelector('[data-v="prec"]'),
    nextButton = viewerElement.querySelector('[data-v="suiv"]');
  if (previousButton) previousButton.onclick = () => goTo(-1);
  if (nextButton) nextButton.onclick = () => goTo(1);
  viewerElement.onkeydown = (event) => {
    if (item === "video") return;
    /* flèches = ±10 s dans le lecteur */ if (event.key === "ArrowLeft" && previousButton) goTo(-1);
    if (event.key === "ArrowRight" && nextButton) goTo(1);
  };
  const jellyfin = viewerElement.querySelector('[data-v="jellyfin"]');
  if (jellyfin) jellyfin.onclick = () => watchInJellyfin(entry).catch((error) => toast(error.message, true));
  if (item === "video")
    startVideo(entry).catch((error) => {
      const playerArea = $("#lecteur");
      if (playerArea) playerArea.innerHTML = `<div class="message-vision"><p>${escapeHtml(error.message)}</p></div>`;
    });
  const media = viewerElement.querySelector("audio");
  if (media)
    media.addEventListener("error", () => {
      viewerElement.querySelector(".corps-vision").innerHTML =
        `<div class="message-vision"><p>Ce format ne se lit pas dans le navigateur.</p><p class="note">${item === "video" ? "Regarde-le dans Jellyfin, ou télécharge-le." : "Télécharge-le pour l'écouter."}</p></div>`;
    });
  if (item === "texte") {
    try {
      const response = await fetch(viewUrl(entry.chemin), { headers: { Range: "bytes=0-1048575" } });
      if (!response.ok) throw new Error(`Erreur ${response.status}`);
      const value = await response.text();
      const preview = viewerElement.querySelector("pre.texte");
      if (preview) preview.textContent = value;
    } catch (error) {
      const preview = viewerElement.querySelector("pre.texte");
      if (preview) preview.textContent = error.message;
    }
  }
}
viewerElement.addEventListener("close", () => {
  if (activePlayer) activePlayer.fin();
  viewerElement.innerHTML = "";
});

// Ouvre ce film dans Jellyfin. La première fois, Jabba demande la clé d'accès Jellyfin.
async function watchInJellyfin(entry) {
  const url = "/api/jellyfin/trouver?chemin=" + encodeURIComponent(entry.chemin);
  let response = await fetch(url, { credentials: "same-origin" });
  let body = await response.json().catch(() => ({}));
  if (response.status === 409 && body.detail && body.detail.cle_jellyfin) {
    const answers = await formBody({
      titre: "Relier Jabba à Jellyfin",
      texte:
        "Une seule fois : dans Jellyfin, Tableau de bord → Clés API → +, nom « Jabba », puis copie la clé et colle-la ici.",
      champs: [{ nom: "cle", label: "Clé API Jellyfin", indice: "32 caractères" }],
      valider: "Relier",
    });
    if (!answers || answers === "secondaire") return;
    await api("/api/jellyfin/cle", { method: "POST", json: { cle: answers.cle } });
    toast("Jabba est relié à Jellyfin");
    response = await fetch(url, { credentials: "same-origin" });
    body = await response.json().catch(() => ({}));
  }
  if (!response.ok)
    throw new Error((body.detail && (body.detail.message || body.detail)) || `Erreur ${response.status}`);
  if (activePlayer) {
    const element = $("#lecteur video");
    if (element) element.pause();
  }
  window.open(body.lien, "_blank", "noopener");
}
