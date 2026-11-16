// Actions sur des fichiers et dossiers : télécharger, renommer, déplacer, mettre à la corbeille,
// avec « Annuler » dans le message plutôt qu'une confirmation avant.
//
// Sommaire :
//   partagé : downloadEntries, renameEntry, moveEntries, deleteEntries, itemChoices, selectionChoices
//   interne : countLabel, undoMoves
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
  if (entries.length === 1 && !entries[0].dossier) {
    location.href = downloadUrl(entries[0].chemin);
    return;
  }
  const query = entries.map((entry) => "chemin=" + encodeURIComponent(entry.chemin)).join("&");
  toast(t("Préparation du .zip… le téléchargement démarre dès qu'il est prêt."));
  location.href = "/api/fichiers/zip?" + query;
}

export async function renameEntry(entry, after) {
  const name = await confirmAction({ titre: t("Renommer"), champ: entry.nom, valider: t("Renommer") });
  if (!name || name === entry.nom) return;
  if (/[\\/]/.test(name)) return toast(t("Le nom ne peut pas contenir / ou \\"), true);
  const target = parentOf(entry.chemin).replace(/\/$/, "") + "/" + name;
  try {
    await api("/api/fichiers/deplacer", { method: "POST", json: { de: entry.chemin, vers: target } });
    toast(t("Renommé"), false, {
      label: t("Annuler"),
      run: () => undoMoves([{ de: entry.chemin, vers: target }], after),
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
      await api("/api/fichiers/deplacer", { method: "POST", json: { de: move.vers, vers: move.de } });
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
      title: entries.length > 1 ? t`Déplacer ${entries.length} éléments` : t`Déplacer « ${entries[0].nom} »`,
      start: parentOf(entries[0].chemin),
      excluded: entries.filter((entry) => entry.dossier).map((entry) => entry.chemin),
    });
  if (!destination) return;
  const inside = entries.find(
    (entry) => entry.dossier && (destination === entry.chemin || destination.startsWith(entry.chemin + "/")),
  );
  if (inside) return toast(t`« ${inside.nom} » ne peut pas aller dans lui-même.`, true);
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
      await api("/api/fichiers/supprimer", { method: "POST", json: { chemin: entry.chemin, dossier: entry.dossier } });
      deleted.push(entry.chemin);
    } catch (error) {
      errors.push(`${entry.nom} : ${error.message}`);
    }
  }
  if (deleted.length)
    toast(t`${countLabel(deleted.length, t("élément mis"), t("éléments mis"))} à la corbeille`, false, {
      label: t("Annuler"),
      run: async () => {
        try {
          const result = await api("/api/fichiers/annuler-suppression", { method: "POST", json: { chemins: deleted } });
          toast(t`${countLabel(result.restaures.length, t("élément remis"), t("éléments remis"))} en place`);
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
    choices.push({ texte: t("Ouvrir"), f: () => (location.hash = context.link(entry.chemin)) });
    if (context.folder.source === "nextcloud" && state.admin)
      choices.push({ texte: t("Partager avec…"), f: () => shareFolder(entry.chemin, entry.nom) });
    if (context.folder.source === "nextcloud" && context.publicOpen)
      choices.push({ texte: t("Demander des fichiers…"), f: () => requestFiles(entry) });
    choices.push({ texte: t("Ajouter à l'accès rapide"), f: () => context.addToQuickAccess(entry.chemin) });
    choices.push({ texte: t("Télécharger (.zip)"), f: () => downloadEntries([entry]) });
  } else {
    if (kindOf(entry.nom)) choices.push({ texte: t("Voir"), f: () => openViewer(context.list, context.index) });
    choices.push({ texte: t("Télécharger"), f: () => downloadEntries([entry]) });
  }
  if (entry.ecriture && context.folder.ecriture) {
    choices.push({ texte: t("Renommer"), f: () => renameEntry(entry, context.after) });
    choices.push({ texte: t("Déplacer vers…"), f: () => moveEntries([entry], context.after) });
    choices.push({ texte: t("Mettre à la corbeille"), danger: true, f: () => deleteEntries([entry], context.after) });
  }
  return choices;
}

// Le menu d'une sélection de plusieurs éléments.
export function selectionChoices(entries, context) {
  const choices = [{ texte: t`Télécharger (${entries.length}, en .zip)`, f: () => downloadEntries(entries) }];
  if (context.folder.ecriture && entries.every((entry) => entry.ecriture)) {
    choices.push({ texte: t("Déplacer vers…"), f: () => moveEntries(entries, context.after) });
    choices.push({ texte: t("Mettre à la corbeille"), danger: true, f: () => deleteEntries(entries, context.after) });
  }
  return choices;
}
