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

// Chaque navigation a un numéro. Un écran lent qui finit après qu'on soit passé à un autre
// a pu écrire par-dessus : on réaffiche alors l'écran demandé en dernier.
let latestNavigation = 0;
let latestFinished = 0;

export async function router() {
  const navigation = ++latestNavigation;
  const [path, query] = (
    location.hash.slice(2) || (state.me && state.me.mode === "invite" ? "invite" : "accueil")
  ).split("?");
  let page = pages[path] ? path : "accueil";
  if (state.me && state.me.mode === "invite" && page !== "aide") page = "invite";
  // Sans réponse du serveur : écran « serveur injoignable » (l'Aide reste ouverte).
  if (!state.me && page !== "aide") return showUnreachable(lastFailure);
  if (page !== "ouvrir") document.body.classList.remove("plein-ecran");
  document.querySelectorAll("[data-page]").forEach((element) => {
    const active =
      element.dataset.page === page ||
      (element.dataset.page === "outils" && page === "ouvrir") ||
      (element.dataset.page === "comptes" && ["nuage", "utilisateurs"].includes(page)) ||
      (element.dataset.page === "plus" &&
        ["disques", "systeme", "journal", "corbeille", "utilisateurs", "invites", "sauvegardes"].includes(page)) ||
      (element.dataset.page === "fichiers" && page === "corbeille") ||
      (element.dataset.page === "systeme" && ["journal", "sauvegardes"].includes(page));
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
    if (navigation === latestNavigation) showError(error);
  }
  if (navigation === latestNavigation) latestFinished = navigation;
  else if (latestFinished === latestNavigation) router();
}
