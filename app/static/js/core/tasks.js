// Tâches longues (mise à jour d'un outil…) suivies à l'écran, et petits blocs « chargement » / « erreur ».
//
// Sommaire :
//   partagé : followTask, loadingHtml, errorHtml
import { toast } from "../components/dialogs.js";
import { api } from "./api.js";
import { escapeHtml } from "./dom.js";
import { t } from "./i18n.js";

export async function followTask(item) {
  toast(`${item.title}…`);
  for (;;) {
    await new Promise((result) => setTimeout(result, 2000));
    const data = await api(`/api/taches/${item.id}`);
    if (data.state !== "en cours") {
      if (data.state === "terminé") toast(t`${data.title} : terminé`);
      else toast(`${data.title} : erreur. ${data.log.slice(-200)}`, true);
      return data;
    }
  }
}

export const loadingHtml = (item = 3) =>
  `<div class="carte pad grille">${'<div class="squelette"></div>'.repeat(item)}</div>`;
export const errorHtml = (entry) => `<div class="erreur">${escapeHtml(entry.message || entry)}</div>`;
