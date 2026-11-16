// Envoyer des dossiers entiers (glissés depuis le Finder, ou choisis avec « Envoyer un dossier »).
//
// Sommaire :
//   partagé : droppedItems, pickedItems, uploadItems
//   interne : readAll, walk
//
// Un « élément » = {file, dir} : le fichier et son sous-dossier relatif (« » à la racine du dépôt).
// Les sous-dossiers sont créés d'abord, du moins profond au plus profond, puis les fichiers partent.
import { api } from "../core/api.js";
import { toast } from "./dialogs.js";
import { uploadFiles } from "./upload_queue.js";
import { t } from "../core/i18n.js";

// Un dossier rend ses entrées par paquets de 100 au plus : on lit jusqu'au bout.
function readAll(reader) {
  return new Promise((resolve, reject) => {
    const all = [];
    const next = () =>
      reader.readEntries((batch) => {
        if (!batch.length) return resolve(all);
        all.push(...batch);
        next();
      }, reject);
    next();
  });
}

async function walk(entry, dir, items) {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    items.push({ file, dir });
  } else if (entry.isDirectory) {
    const inner = dir ? `${dir}/${entry.name}` : entry.name;
    items.push({ file: null, dir: inner }); // dossier vide compris
    for (const child of await readAll(entry.createReader())) await walk(child, inner, items);
  }
}

// Ce qui a été glissé (fichiers et dossiers).
export async function droppedItems(dataTransfer) {
  const entries = [...(dataTransfer.items || [])]
    .map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null))
    .filter(Boolean);
  if (!entries.length) return [...dataTransfer.files].map((file) => ({ file, dir: "" }));
  const items = [];
  for (const entry of entries) await walk(entry, "", items);
  return items;
}

// Ce qui a été choisi avec un sélecteur de dossier (<input webkitdirectory>).
export function pickedItems(fileList) {
  return [...fileList].map((file) => {
    const parts = (file.webkitRelativePath || file.name).split("/");
    parts.pop();
    return { file, dir: parts.join("/") };
  });
}

export async function uploadItems(path, items, after) {
  const base = path.replace(/\/$/, "");
  const folders = [...new Set(items.map((item) => item.dir).filter(Boolean))];
  // « a/b/c » demande aussi « a » et « a/b »
  const needed = [
    ...new Set(folders.flatMap((dir) => dir.split("/").map((_, i, all) => all.slice(0, i + 1).join("/")))),
  ];
  needed.sort((a, b) => a.split("/").length - b.split("/").length);
  for (const dir of needed) {
    try {
      await api("/api/fichiers/dossier", { method: "POST", json: { chemin: `${base}/${dir}` } });
    } catch (error) {
      if (!/existe|déjà/i.test(error.message)) {
        toast(t`Dossier « ${dir} » : ${error.message}`, true);
        return;
      }
    }
  }
  if (needed.length) after();
  const byFolder = new Map();
  items
    .filter((item) => item.file)
    .forEach((item) => {
      const target = item.dir ? `${base}/${item.dir}` : base || "/";
      if (!byFolder.has(target)) byFolder.set(target, []);
      byFolder.get(target).push(item.file);
    });
  byFolder.forEach((files, target) => uploadFiles(target, files, after));
}
