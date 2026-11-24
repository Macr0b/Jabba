// Cibles de dépôt : un dossier (ligne, tuile, accès rapide, fil d'Ariane) accepte
//   - des fichiers et des dossiers glissés depuis le Finder → envoi ;
//   - des éléments glissés depuis la liste de Jabba → déplacement.
//
// Sommaire :
//   partagé : enableDrop, startDrag, JABBA_DRAG
//   interne : accepts
import { moveEntries } from "./file_actions.js";
import { droppedItems, uploadItems } from "./folder_upload.js";

export const JABBA_DRAG = "application/x-jabba-elements";
let dragged = []; // les éléments en cours de glisser (dataTransfer n'est lisible qu'au dépôt)

const accepts = (event) => event.dataTransfer.types.includes("Files") || event.dataTransfer.types.includes(JABBA_DRAG);

// À appeler au début d'un glisser depuis la liste : `entries` = l'élément, ou toute la sélection.
export function startDrag(event, entries) {
  dragged = entries;
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData(JABBA_DRAG, entries.map((entry) => entry.path).join("\n"));
  event.dataTransfer.setData("text/plain", entries.map((entry) => entry.name).join(", "));
}

export function enableDrop(element, path, after) {
  let counter = 0;
  element.addEventListener("dragenter", (event) => {
    if (!accepts(event)) return;
    event.preventDefault();
    counter++;
    element.classList.add("cible");
  });
  element.addEventListener("dragover", (event) => {
    if (!accepts(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = event.dataTransfer.types.includes(JABBA_DRAG) ? "move" : "copy";
  });
  element.addEventListener("dragleave", () => {
    counter = Math.max(0, counter - 1);
    if (!counter) element.classList.remove("cible");
  });
  element.addEventListener("drop", async (event) => {
    if (!accepts(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.jabbaPris = true;
    counter = 0;
    element.classList.remove("cible");
    if (event.dataTransfer.types.includes(JABBA_DRAG)) {
      const entries = dragged.filter((entry) => entry.path !== path);
      dragged = [];
      if (entries.length) moveEntries(entries, after, path);
      return;
    }
    uploadItems(path, await droppedItems(event.dataTransfer), after);
  });
}
