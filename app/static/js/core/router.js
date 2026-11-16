// Navigation : affiche l'écran demandé par l'adresse (#/fichiers…), ou l'erreur.
//
// Sommaire :
//   partagé : showError, router
import { content } from "./dom.js";
import { pages } from "./pages.js";
import { state } from "./state.js";
import { errorHtml } from "./tasks.js";
import { lastFailure, showUnreachable } from "../pages/help.js";

// ------------------------------------------------------------ navigation
state.me = null;
export function showError(entry) {
  if (entry && entry.injoignable) return showUnreachable(entry.injoignable);
  content.innerHTML = `<div class="entete"><div><h1>Oups</h1></div></div>${errorHtml(entry)}`;
}

export async function router() {
  const [path, query] = (
    location.hash.slice(2) || (state.me && state.me.mode === "invite" ? "invite" : "accueil")
  ).split("?");
  let page = pages[path] ? path : "accueil";
  if (state.me && state.me.mode === "invite" && page !== "aide") page = "invite";
  if (!state.me && page !== "aide") return showUnreachable(lastFailure); // serveur injoignable   // sans connexion admin : seulement la page invités
  if (page !== "ouvrir") document.body.classList.remove("plein-ecran");
  document.querySelectorAll("[data-page]").forEach((element) => {
    const active =
      element.dataset.page === page ||
      (element.dataset.page === "outils" && page === "ouvrir") ||
      (element.dataset.page === "comptes" && ["nuage", "utilisateurs"].includes(page)) ||
      (element.dataset.page === "plus" &&
        ["disques", "systeme", "journal", "corbeille", "utilisateurs", "invites", "terminal", "sauvegardes"].includes(
          page,
        )) ||
      (element.dataset.page === "fichiers" && page === "corbeille") ||
      (element.dataset.page === "systeme" && ["journal", "terminal", "sauvegardes"].includes(page));
    element.classList.toggle("actif", active);
    if (active) element.setAttribute("aria-current", "page");
    else element.removeAttribute("aria-current");
  });
  document.title =
    "Jabba · " +
    ({ outils: "Établi", ouvrir: "Outil", nuage: "Comptes Nextcloud" }[page] || page[0].toUpperCase() + page.slice(1));
  try {
    await pages[page](new URLSearchParams(query || ""));
  } catch (error) {
    showError(error);
  }
}
