// Tâches longues (mise à jour d'un outil…) suivies à l'écran, et petits blocs « chargement » / « erreur ».
//
// Sommaire :
//   partagé : followTask, loadingHtml, errorHtml
import { toast } from "../components/dialogs.js";
import { api } from "./api.js";
import { escapeHtml } from "./dom.js";

export async function followTask(item) {
  toast(`${item.titre}…`);
  for (;;) {
    await new Promise((result) => setTimeout(result, 2000));
    const data = await api(`/api/taches/${item.id}`);
    if (data.etat !== "en cours") {
      if (data.etat === "terminé") toast(`${data.titre} : terminé`);
      else toast(`${data.titre} : erreur. ${data.journal.slice(-200)}`, true);
      return data;
    }
  }
}

export const loadingHtml = (item = 3) =>
  `<div class="carte pad grille">${'<div class="squelette"></div>'.repeat(item)}</div>`;
export const errorHtml = (entry) => `<div class="erreur">${escapeHtml(entry.message || entry)}</div>`;
