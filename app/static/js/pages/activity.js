// Écran Journal : ce qui s'est passé sur le serveur.
//
// Sommaire :
//   partagé : activityLine, pages.journal (écran)
//   interne : FAMILIES
import { api } from "../core/api.js";
import { content, escapeHtml } from "../core/dom.js";
import { formatWhen } from "../core/format.js";
import { pages } from "../core/pages.js";
import { loadingHtml } from "../core/tasks.js";
import { t } from "../core/i18n.js";

// ------------------------------------------------------------ journal
const FAMILIES = {
  outil: t("Outil"),
  admin: t("Admin"),
  disque: t("Disque"),
  courant: t("Courant"),
  fichier: t("Fichier"),
  appli: t("Appli"),
  alerte: t("Alerte"),
  sauvegardes: t("Sauvegardes"),
  nuage: t("Nextcloud"),
  systeme: t("Système"),
};
export const activityLine = (event) => `<div class="evenement f-${escapeHtml(event.famille)}">
    <span class="famille">${FAMILIES[event.famille] || t("Système")}</span>
    <span class="texte-ev">${escapeHtml(event.texte)}${event.qui ? ` <span class="note">· ${escapeHtml(event.qui)}</span>` : ""}</span>
    <span class="note quand-ev">${formatWhen(new Date(event.t * 1000).toISOString())}</span></div>`;

pages.journal = async (params) => {
  const family = params.get("famille") || "";
  content.innerHTML = t`<div class="entete"><div><h1>Journal</h1></div></div>${loadingHtml(5)}`;
  const events = await api("/api/journal?n=300" + (family ? "&famille=" + encodeURIComponent(family) : ""));
  content.innerHTML = t`
    <div class="entete"><div><h1>Journal</h1><p class="sous-titre">Ce qui s'est passé sur le serveur</p></div>
      <div class="actions"><a class="btn" href="#/systeme">← Système</a></div></div>
    <div class="filtres">${[["", t("Tout")], ...Object.entries(FAMILIES)].map(([key, value]) => `<a class="filtre${key === family ? " actif" : ""}" href="#/journal${key ? "?famille=" + key : ""}">${value}</a>`).join("")}</div>
    <section class="carte pad journal">${events.length ? events.map(activityLine).join("") : t('<p class="note">Rien pour l\'instant.</p>')}</section>`;
};
