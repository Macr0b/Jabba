// Actions sur des fichiers et dossiers : télécharger, renommer, déplacer, mettre à la corbeille,
// avec « Annuler » dans le message plutôt qu'une confirmation avant.
//
// Sommaire :
//   partagé : downloadEntries, renameEntry, moveEntries, deleteEntries, itemChoices, selectionChoices
//   interne : countLabel, undoMoves, parentOf
import { api } from "../core/api.js";
import { state } from "../core/state.js";
import { confirmAction, toast } from "./dialogs.js";
import { pickFolder } from "./folder_picker.js";
import { requestFiles } from "./public_mode_card.js";
import { shareFolder } from "./share.js";
import { downloadUrl, kindOf, openViewer } from "./viewer.js";
import { t } from "../core/i18n.js";

const countLabel = (count, one, many) => `${count} ${count > 1 ? many : one}`;
const parentOf = (path) => path.replace(/\/[^/]*$/, "") || "/";

// Un fichier : téléchargement direct. Plusieurs éléments, ou un dossier : un seul .zip.
export function downloadEntries(entries) {
  if (entries.length === 1 && !entries[0].folder) {
    location.href = downloadUrl(entries[0].path);
    return;
  }
  const query = entries.map((entry) => "chemin=" + encodeURIComponent(entry.path)).join("&");
  toast(t("Préparation du .zip… le téléchargement démarre dès qu'il est prêt."));
  location.href = "/api/fichiers/zip?" + query;
}

export async function renameEntry(entry, after) {
  const name = await confirmAction({ title: t("Renommer"), field: entry.name, confirmLabel: t("Renommer") });
  if (!name || name === entry.name) return;
  if (/[\\/]/.test(name)) return toast(t("Le nom ne peut pas contenir / ou \\"), true);
  const target = parentOf(entry.path).replace(/\/$/, "") + "/" + name;
  try {
    await api("/api/fichiers/deplacer", { method: "POST", json: { source: entry.path, destination: target } });
    toast(t("Renommé"), false, {
      label: t("Annuler"),
      run: () => undoMoves([{ source: entry.path, destination: target }], after),
    });
  } catch (error) {
    toast(error.message, true);
  }
  after();
}

async function undoMoves(moves, after) {
  let failed = 0;
  for (const move of moves) {
    try {
      await api("/api/fichiers/deplacer", {
        method: "POST",
        json: { source: move.destination, destination: move.source },
      });
    } catch (_) {
      failed++;
    }
  }
  toast(failed ? t`${failed} élément(s) n'ont pas pu revenir` : t("Annulé"), Boolean(failed));
  after();
}

// `destination` donné (dépôt sur un dossier) : pas de question. Sinon, le sélecteur de dossier.
export async function moveEntries(entries, after, destination = null) {
  if (destination === null)
    destination = await pickFolder({
      title: entries.length > 1 ? t`Déplacer ${entries.length} éléments` : t`Déplacer « ${entries[0].name} »`,
      start: parentOf(entries[0].path),
      excluded: entries.filter((entry) => entry.folder).map((entry) => entry.path),
    });
  if (!destination) return;
  const inside = entries.find(
    (entry) => entry.folder && (destination === entry.path || destination.startsWith(entry.path + "/")),
  );
  if (inside) return toast(t`« ${inside.name} » ne peut pas aller dans lui-même.`, true);
  const folder = destination.replace(/\/$/, "");
  const moves = [];
  const errors = [];
  for (const entry of entries) {
    const target = folder + "/" + entry.name;
    if (target === entry.path) continue;
    try {
      await api("/api/fichiers/deplacer", { method: "POST", json: { source: entry.path, destination: target } });
      moves.push({ source: entry.path, destination: target });
    } catch (error) {
      errors.push(`${entry.name} : ${error.message}`);
    }
  }
  if (moves.length)
    toast(`${countLabel(moves.length, t("élément déplacé"), t("éléments déplacés"))}`, false, {
      label: t("Annuler"),
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
      await api("/api/fichiers/supprimer", { method: "POST", json: { path: entry.path, folder: entry.folder } });
      deleted.push(entry.path);
    } catch (error) {
      errors.push(`${entry.name} : ${error.message}`);
    }
  }
  if (deleted.length)
    toast(t`${countLabel(deleted.length, t("élément mis"), t("éléments mis"))} à la corbeille`, false, {
      label: t("Annuler"),
      run: async () => {
        try {
          const result = await api("/api/fichiers/annuler-suppression", { method: "POST", json: { paths: deleted } });
          toast(t`${countLabel(result.restored.length, t("élément remis"), t("éléments remis"))} en place`);
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
  if (entry.folder) {
    choices.push({ text: t("Ouvrir"), f: () => (location.hash = context.link(entry.path)) });
    if (context.folder.source === "nextcloud" && state.admin)
      choices.push({ text: t("Partager avec…"), f: () => shareFolder(entry.path, entry.name) });
    if (context.folder.source === "nextcloud" && context.publicOpen)
      choices.push({ text: t("Demander des fichiers…"), f: () => requestFiles(entry) });
    choices.push({ text: t("Ajouter à l'accès rapide"), f: () => context.addToQuickAccess(entry.path) });
    choices.push({ text: t("Télécharger (.zip)"), f: () => downloadEntries([entry]) });
  } else {
    if (kindOf(entry.name)) choices.push({ text: t("Voir"), f: () => openViewer(context.list, context.index) });
    choices.push({ text: t("Télécharger"), f: () => downloadEntries([entry]) });
  }
  if (entry.writable && context.folder.writable) {
    choices.push({ text: t("Renommer"), f: () => renameEntry(entry, context.after) });
    choices.push({ text: t("Déplacer vers…"), f: () => moveEntries([entry], context.after) });
    choices.push({ text: t("Mettre à la corbeille"), danger: true, f: () => deleteEntries([entry], context.after) });
  }
  return choices;
}

// Le menu d'une sélection de plusieurs éléments.
export function selectionChoices(entries, context) {
  const choices = [{ text: t`Télécharger (${entries.length}, en .zip)`, f: () => downloadEntries(entries) }];
  if (context.folder.writable && entries.every((entry) => entry.writable)) {
    choices.push({ text: t("Déplacer vers…"), f: () => moveEntries(entries, context.after) });
    choices.push({ text: t("Mettre à la corbeille"), danger: true, f: () => deleteEntries(entries, context.after) });
  }
  return choices;
}
