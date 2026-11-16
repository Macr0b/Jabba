// Mode invité : ce que voient les personnes sans compte (dossiers partagés, outils ouverts).
//
// Sommaire :
//   partagé : pages.invite (écran)
//   interne : guestFileUrl, showGuestFolder
import { toast } from "../components/dialogs.js";
import { THUMBNAIL_EXTENSIONS, extensionOf, kindOf } from "../components/viewer.js";
import { adminLogin } from "../core/admin_session.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatBytes, formatWhen } from "../core/format.js";
import { pages } from "../core/pages.js";
import { showError, router } from "../core/router.js";
import { state } from "../core/state.js";

// ------------------------------------------------------------ mode invité (sans connexion admin)
const guestFileUrl = (data, path, view = 0) =>
  `/api/invite/fichier?d=${encodeURIComponent(data)}&chemin=${encodeURIComponent(path)}${view ? "&voir=1" : ""}`;

pages.invite = async (params) => {
  const data = await api("/api/invite/etat");
  const title = state.me && state.me.serveur ? state.me.serveur[0].toUpperCase() + state.me.serveur.slice(1) : "Jabba";
  const adminFooter = data.lan
    ? `<p class="note centre">Administration : depuis l'appli Jabba, par Tailscale.</p>`
    : `<div class="centre"><button class="btn btn-p" id="connexion">Connexion admin</button></div>`;
  if (!data.actif) {
    content.innerHTML = `<div class="invite-accueil"><span class="logo grand">J</span><h1>${escapeHtml(title)}</h1><p class="sous-titre">L'accès invités est fermé pour le moment.</p></div>${adminFooter}`;
  } else if (!data.entre) {
    content.innerHTML = `<div class="invite-accueil"><span class="logo grand">J</span><h1>Bienvenue chez ${escapeHtml(title)}</h1>
        <p class="sous-titre">Tape le code d'accès à 4 chiffres (ou scanne le QR code).</p>
        <form id="form-code" class="form-code"><input name="code" inputmode="numeric" pattern="[0-9]*" maxlength="4" autocomplete="one-time-code" aria-label="Code d'accès" placeholder="••••"><button class="btn btn-p" type="submit">Entrer</button></form></div>${adminFooter}`;
    $("#form-code").onsubmit = async (event) => {
      event.preventDefault();
      try {
        await api("/api/invite/entrer", { method: "POST", json: { code: event.target.code.value } });
        router();
      } catch (error) {
        toast(error.message, true);
      }
    };
    $("#form-code").code.focus();
  } else if (params.get("d")) {
    return showGuestFolder(params.get("d"), params.get("chemin") || "/");
  } else {
    content.innerHTML = `<div class="entete"><div><h1>${escapeHtml(title)}</h1><p class="sous-titre">${data.lan ? "Bienvenue ! Voici ce qui est partagé avec toi." : "Mode invité : ce que voient les invités. Connecte-toi pour tout le reste."}</p></div></div>
      <section aria-label="Dossiers partagés"><h2 class="titre-section">Dossiers</h2>
        <div class="tuiles">${data.dossiers.map((item) => `<a class="carte tuile" href="#/invite?d=${encodeURIComponent(item.id)}"><span class="ic-tuile dossier" aria-hidden="true"></span><span class="nom-tuile">${escapeHtml(item.nom)}</span><span class="note">${item.mode === "depot" ? "Lire et déposer" : "Lecture seule"}</span></a>`).join("") || '<p class="note">Aucun dossier partagé.</p>'}</div></section>
      ${data.outils.length ? `<section aria-label="Outils"><h2 class="titre-section">Outils</h2><section class="carte liste-raccourcis">${data.outils.map((item) => `<a href="${escapeHtml(item.lien)}" target="_blank" rel="noopener"><span>${escapeHtml(item.nom)} <span class="note">· ${escapeHtml(item.role)}</span></span><span>↗</span></a>`).join("")}</section></section>` : ""}
      ${adminFooter}`;
  }
  const button = $("#connexion");
  if (button) button.onclick = () => adminLogin();
};

async function showGuestFolder(folderId, path) {
  const data = await api(`/api/invite/fichiers?d=${encodeURIComponent(folderId)}&chemin=${encodeURIComponent(path)}`);
  const link = (item) => `#/invite?d=${encodeURIComponent(folderId)}&chemin=${encodeURIComponent(item)}`;
  const parts = path.split("/").filter(Boolean);
  const thread = [
    `<a href="#/invite">Accueil</a>`,
    parts.length
      ? `<a href="${link("/")}">${escapeHtml(data.dossier.nom)}</a>`
      : `<strong>${escapeHtml(data.dossier.nom)}</strong>`,
  ]
    .concat(
      parts.map((value, index) =>
        index === parts.length - 1
          ? `<strong>${escapeHtml(value)}</strong>`
          : `<a href="${link("/" + parts.slice(0, index + 1).join("/"))}">${escapeHtml(value)}</a>`,
      ),
    )
    .join('<span class="sep">/</span>');
  const dropZone = data.dossier.mode === "depot";
  content.innerHTML = `
    <div class="entete"><div><h1>${escapeHtml(parts.length ? parts[parts.length - 1] : data.dossier.nom)}${dropZone ? "" : '<span class="lecture-seule">Lecture seule</span>'}</h1></div>
      ${dropZone ? `<div class="actions"><button class="btn btn-p" id="envoyer">Déposer des fichiers</button></div>` : ""}</div>
    <nav class="fil" aria-label="Emplacement">${thread}</nav>
    <section class="carte tableau">
      ${
        data.elements.length
          ? data.elements
              .map(
                (item) => `<div class="ligne">
        <span class="nom">${item.dossier ? '<span class="ic-dossier" aria-hidden="true"></span>' : THUMBNAIL_EXTENSIONS.includes(extensionOf(item.nom)) ? `<span class="vignette" aria-hidden="true"><img alt="" loading="lazy" src="${guestFileUrl(folderId, item.chemin, 1)}" onerror="this.parentNode.className='ic-fichier';this.remove()"></span>` : `<span class="ic-fichier${kindOf(item.nom) ? " " + kindOf(item.nom) : ""}" aria-hidden="true"></span>`}
          <span>${item.dossier ? `<a href="${link(item.chemin)}">${escapeHtml(item.nom)}</a>` : `<a href="${guestFileUrl(folderId, item.chemin)}">${escapeHtml(item.nom)}</a>`}<span class="mobile-detail">${item.dossier ? "" : formatBytes(item.taille)}</span></span></span>
        <span class="gris col-l">${item.dossier ? "" : formatBytes(item.taille)}</span><span class="gris col-l">${formatWhen(item.modifie)}</span>
        ${item.dossier ? "<span></span>" : `<a class="btn btn-petit" href="${guestFileUrl(folderId, item.chemin)}" aria-label="Télécharger ${escapeHtml(item.nom)}">↓</a>`}</div>`,
              )
              .join("")
          : '<div class="vide">Ce dossier est vide.</div>'
      }
      ${dropZone ? `<div class="depot" id="depot">Glisse des fichiers ici pour les déposer</div>` : ""}
    </section><input type="file" id="choix" multiple hidden>`;
  if (!dropZone) return;
  const send = async (files) => {
    files = files.filter((item) => item.size || item.type);
    if (!files.length) return;
    const formData = new FormData();
    files.forEach((item) => formData.append("fichiers", item, item.name));
    toast(`Envoi de ${files.length} fichier${files.length > 1 ? "s" : ""}…`);
    try {
      const data = await api(
        `/api/invite/envoyer?d=${encodeURIComponent(folderId)}&chemin=${encodeURIComponent(path)}`,
        {
          method: "POST",
          body: formData,
        },
      );
      toast(`${data.envoyes.length} fichier${data.envoyes.length > 1 ? "s déposés" : " déposé"}, merci !`);
    } catch (error) {
      toast(error.message, true);
    }
    showGuestFolder(folderId, path).catch(showError);
  };
  const choices = $("#choix");
  $("#envoyer").onclick = () => choices.click();
  choices.onchange = () => send([...choices.files]);
  const area = $(".tableau"),
    dropButton = $("#depot");
  ["dragenter", "dragover"].forEach((value) =>
    area.addEventListener(value, (event) => {
      event.preventDefault();
      dropButton.classList.add("survol");
    }),
  );
  ["dragleave", "drop"].forEach((value) =>
    area.addEventListener(value, (event) => {
      event.preventDefault();
      if (value === "drop" || !area.contains(event.relatedTarget)) dropButton.classList.remove("survol");
    }),
  );
  area.addEventListener("drop", (event) => send([...event.dataTransfer.files]));
}
