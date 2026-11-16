// Accès à la page : $ (chercher un élément), la zone de contenu, et escapeHtml (texte sûr dans du HTML).
//
// Sommaire :
//   partagé : $, content, escapeHtml

/* Appli Jabba — interface. Aucune dépendance externe. */

export const $ = (value, result = document) => result.querySelector(value);
export const content = $("#contenu");
export const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (item) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[item],
  );
