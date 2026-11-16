// Actions sur des fichiers et dossiers : télécharger, renommer, déplacer, mettre à la corbeille,
// avec « Annuler » dans le message plutôt qu'une confirmation avant.
//
// Sommaire :
//   partagé : downloadEntries, renameEntry, moveEntries, deleteEntries, itemChoices, selectionChoices
//   interne : countLabel, undoMoves
import { api } from "../core/api.js";
import { state } from "../core/state.js";
import { confirmAction, toast } from "./dialogs.js";
import { requestFiles } from "./public_mode_card.js";
import { shareFolder } from "./share.js";
import { downloadUrl, kindOf, openViewer } from "./viewer.js";

const countLabel = (count, one, many) => `${count} ${count > 1 ? many : one}`;
const parentOf = (path) => path.replace(/\/[^/]*$/, "") || "/";

// Un fichier : téléchargement direct. Plusieurs éléments, ou un dossier : un seul .zip.
export function downloadEntries(entries) {
  if (entries.length === 1 && !entries[0].dossier) {
    location.href = downloadUrl(entries[0].chemin);
    return;
  }
  const query = entries.map((entry) => "chemin=" + encodeURIComponent(entry.chemin)).join("&");
  toast("Préparation du .zip… le téléchargement démarre dès qu'il est prêt.");
  location.href = "/api/fichiers/zip?" + query;
}

export async function renameEntry(entry, after) {
  const name = await confirmAction({ titre: "Renommer", champ: entry.nom, valider: "Renommer" });
  if (!name || name === entry.nom) return;
  if (/[\\/]/.test(name)) return toast("Le nom ne peut pas contenir / ou \\", true);
  const target = parentOf(entry.chemin).replace(/\/$/, "") + "/" + name;
  try {
    await api("/api/fichiers/deplacer", { method: "POST", json: { de: entry.chemin, vers: target } });
    toast("Renommé", false, { label: "Annuler", run: () => undoMoves([{ de: entry.chemin, vers: target }], after) });
  } catch (error) {
    toast(error.message, true);
  }
  after();
}

async function undoMoves(moves, after) {
  let failed = 0;
  for (const move of moves) {
    try {
      await api("/api/fichiers/deplacer", { method: "POST", json: { de: move.vers, vers: move.de } });
    } catch (_) {
      failed++;
    }
  }
  toast(failed ? `${failed} élément(s) n'ont pas pu revenir` : "Annulé", Boolean(failed));
  after();
}

export async function moveEntries(entries, after) {
  const destination = await confirmAction({
    titre: entries.length > 1 ? `Déplacer ${entries.length} éléments vers` : `Déplacer « ${entries[0].nom} » vers`,
    texte: "Chemin du dossier de destination, par exemple /Documents/Archives",
    champ: parentOf(entries[0].chemin),
    valider: "Déplacer",
  });
  if (!destination) return;
  const folder = destination.replace(/\/$/, "");
  const moves = [];
  const errors = [];
  for (const entry of entries) {
    const target = folder + "/" + entry.nom;
    if (target === entry.chemin) continue;
    try {
      await api("/api/fichiers/deplacer", { method: "POST", json: { de: entry.chemin, vers: target } });
      moves.push({ de: entry.chemin, vers: target });
    } catch (error) {
      errors.push(`${entry.nom} : ${error.message}`);
    }
  }
  if (moves.length)
    toast(`${countLabel(moves.length, "élément déplacé", "éléments déplacés")}`, false, {
      label: "Annuler",
      run: () => undoMoves(moves, after),
    });
  if (errors.length) toast(errors.join(" · "), true);
  after();
}

// Pas de confirmation : tout part dans la corbeille, et « Annuler » remet en place.
export async function deleteEntries(entries, after) {
  const deleted = [];
  const errors = [];
  for (const entry of entries) {
    try {
      await api("/api/fichiers/supprimer", { method: "POST", json: { chemin: entry.chemin, dossier: entry.dossier } });
      deleted.push(entry.chemin);
    } catch (error) {
      errors.push(`${entry.nom} : ${error.message}`);
    }
  }
  if (deleted.length)
    toast(`${countLabel(deleted.length, "élément mis", "éléments mis")} à la corbeille`, false, {
      label: "Annuler",
      run: async () => {
        try {
          const result = await api("/api/fichiers/annuler-suppression", { method: "POST", json: { chemins: deleted } });
          toast(`${countLabel(result.restaures.length, "élément remis", "éléments remis")} en place`);
        } catch (error) {
          toast(error.message, true);
        }
        after();
      },
    });
  if (errors.length) toast(errors.join(" · "), true);
  after();
}

// Le menu d'un élément (bouton •••, clic droit) : context = {list, index, folder, after, link, publicOpen}.
export function itemChoices(entry, context) {
  const choices = [];
  if (entry.dossier) {
    choices.push({ texte: "Ouvrir", f: () => (location.hash = context.link(entry.chemin)) });
    if (context.folder.source === "nextcloud" && state.admin)
      choices.push({ texte: "Partager avec…", f: () => shareFolder(entry.chemin, entry.nom) });
    if (context.folder.source === "nextcloud" && context.publicOpen)
      choices.push({ texte: "Demander des fichiers…", f: () => requestFiles(entry) });
    choices.push({ texte: "Ajouter à l'accès rapide", f: () => context.addToQuickAccess(entry.chemin) });
    choices.push({ texte: "Télécharger (.zip)", f: () => downloadEntries([entry]) });
  } else {
    if (kindOf(entry.nom)) choices.push({ texte: "Voir", f: () => openViewer(context.list, context.index) });
    choices.push({ texte: "Télécharger", f: () => downloadEntries([entry]) });
  }
  if (entry.ecriture && context.folder.ecriture) {
    choices.push({ texte: "Renommer", f: () => renameEntry(entry, context.after) });
    choices.push({ texte: "Déplacer vers…", f: () => moveEntries([entry], context.after) });
    choices.push({ texte: "Mettre à la corbeille", danger: true, f: () => deleteEntries([entry], context.after) });
  }
  return choices;
}

// Le menu d'une sélection de plusieurs éléments.
export function selectionChoices(entries, context) {
  const choices = [{ texte: `Télécharger (${entries.length}, en .zip)`, f: () => downloadEntries(entries) }];
  if (context.folder.ecriture && entries.every((entry) => entry.ecriture)) {
    choices.push({ texte: "Déplacer vers…", f: () => moveEntries(entries, context.after) });
    choices.push({ texte: "Mettre à la corbeille", danger: true, f: () => deleteEntries(entries, context.after) });
  }
  return choices;
}
