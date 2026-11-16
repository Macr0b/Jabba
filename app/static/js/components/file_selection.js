// Sélection de plusieurs fichiers, barre d'actions, clic droit et raccourcis clavier de l'écran Fichiers.
//
// Sommaire :
//   partagé : attachSelection, SHORTCUTS
//   interne : current, items, setSelected, refresh, renderBar, openMenu, moveFocus, columnCount, onKey
//
// Souris : case à cocher, ⌘/Ctrl + clic pour ajouter, Maj + clic pour une plage, clic droit = menu.
// Téléphone : appui long sur un élément pour commencer une sélection.
// Clavier : voir SHORTCUTS (affiché dans l'aide de l'écran).
import { $, content } from "../core/dom.js";
import { chooseFromMenu, dialogElement } from "./dialogs.js";
import {
  deleteEntries,
  downloadEntries,
  itemChoices,
  moveEntries,
  renameEntry,
  selectionChoices,
} from "./file_actions.js";

export const SHORTCUTS = [
  ["↑ ↓ ← →", "se déplacer"],
  ["Entrée", "ouvrir"],
  ["Espace", "sélectionner"],
  ["⌘A / Ctrl+A", "tout sélectionner"],
  ["Suppr ou ⌘⌫", "mettre à la corbeille"],
  ["F2", "renommer"],
  ["/", "chercher"],
  ["Échap", "tout désélectionner"],
];
const LONG_PRESS_MS = 550;
let current = null; // {entries, context, selected: Set, anchor, focus}

const items = () => [...content.querySelectorAll(".element[data-i]")];
const selectedEntries = () => [...current.selected].sort((a, b) => a - b).map((index) => current.entries[index]);

function setSelected(index, selected) {
  if (selected) current.selected.add(index);
  else current.selected.delete(index);
}

function refresh() {
  items().forEach((element) => {
    const selected = current.selected.has(+element.dataset.i);
    element.classList.toggle("choisi", selected);
    const box = element.querySelector("input[data-choix]");
    if (box) box.checked = selected;
  });
  document.body.classList.toggle("selection-active", current.selected.size > 0);
  renderBar();
}

function renderBar() {
  let bar = $("#barre-selection");
  if (!current.selected.size) {
    if (bar) bar.remove();
    return;
  }
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "barre-selection";
    bar.className = "barre-selection";
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", "Actions sur la sélection");
    content.appendChild(bar);
  }
  const count = current.selected.size;
  const writable = current.context.folder.ecriture && selectedEntries().every((entry) => entry.ecriture);
  bar.innerHTML = `<strong>${count} sélectionné${count > 1 ? "s" : ""}</strong>
    <button class="btn btn-petit" type="button" data-action="telecharger">Télécharger</button>
    ${writable ? '<button class="btn btn-petit" type="button" data-action="deplacer">Déplacer</button><button class="btn btn-petit btn-danger" type="button" data-action="corbeille">Corbeille</button>' : ""}
    <button class="btn btn-petit" type="button" data-action="aucun" aria-label="Tout désélectionner">✕</button>`;
  bar.querySelectorAll("[data-action]").forEach((button) => {
    button.onclick = () => runOnSelection(button.dataset.action);
  });
}

function runOnSelection(action) {
  const entries = selectedEntries();
  const after = current.context.after;
  if (action === "telecharger") downloadEntries(entries);
  else if (action === "deplacer") moveEntries(entries, after);
  else if (action === "corbeille") deleteEntries(entries, after);
  else {
    current.selected.clear();
    refresh();
  }
}

async function openMenu(index) {
  const multiple = current.selected.size > 1 && current.selected.has(index);
  const entries = multiple ? selectedEntries() : [current.entries[index]];
  const choices = multiple
    ? selectionChoices(entries, current.context)
    : itemChoices(entries[0], { ...current.context, list: current.entries, index });
  const choice = await chooseFromMenu(multiple ? `${entries.length} éléments` : entries[0].nom, choices);
  if (choice) setTimeout(choice.f, 50);
}

function columnCount() {
  const all = items();
  if (all.length < 2) return 1;
  const top = all[0].offsetTop;
  const sameRow = all.filter((element) => element.offsetTop === top).length;
  return Math.max(1, sameRow);
}

function moveFocus(step, extend) {
  const all = items();
  if (!all.length) return;
  const position = Math.min(all.length - 1, Math.max(0, (current.focus ?? -1) + step));
  current.focus = position;
  all[position].focus();
  if (extend) {
    setSelected(position, true);
    refresh();
  }
}

function onKey(event) {
  if (!current || !location.hash.startsWith("#/fichiers") || dialogElement.open) return;
  const target = event.target;
  const typing = /INPUT|TEXTAREA|SELECT/.test(target.tagName) && !(target.type === "checkbox");
  if (typing) return;
  const command = event.metaKey || event.ctrlKey;
  const focused = current.focus != null ? current.entries[current.focus] : null;
  const columns = content.querySelector(".grille-fichiers") ? columnCount() : 1;
  const handlers = {
    ArrowDown: () => moveFocus(columns, event.shiftKey),
    ArrowUp: () => moveFocus(-columns, event.shiftKey),
    ArrowRight: () => columns > 1 && moveFocus(1, event.shiftKey),
    ArrowLeft: () => columns > 1 && moveFocus(-1, event.shiftKey),
    Enter: () => focused && current.context.open(current.focus),
    " ": () => {
      if (current.focus == null) return;
      setSelected(current.focus, !current.selected.has(current.focus));
      refresh();
    },
    Escape: () => {
      current.selected.clear();
      refresh();
    },
    F2: () =>
      focused && focused.ecriture && current.context.folder.ecriture && renameEntry(focused, current.context.after),
    "/": () => {
      const search = content.querySelector("#form-recherche input");
      if (search) search.focus();
    },
  };
  if (command && event.key.toLowerCase() === "a") {
    event.preventDefault();
    current.entries.forEach((_, index) => current.selected.add(index));
    return refresh();
  }
  if (event.key === "Delete" || (command && event.key === "Backspace")) {
    const entries = current.selected.size ? selectedEntries() : focused ? [focused] : [];
    if (!entries.length || !current.context.folder.ecriture || !entries.every((entry) => entry.ecriture)) return;
    event.preventDefault();
    return deleteEntries(entries, current.context.after);
  }
  if (command || event.altKey) return; // ⌘[ ⌘] ⌘↑ : historique, géré par l'écran Fichiers
  const handler = handlers[event.key];
  if (!handler) return;
  if (event.key !== "Enter" || target.tagName !== "A") event.preventDefault();
  handler();
}
document.addEventListener("keydown", onKey);

// À appeler après chaque affichage de la liste. context = {folder, after, open(index), link, addToQuickAccess}.
export function attachSelection(entries, context) {
  current = { entries, context, selected: new Set(), anchor: null, focus: null };
  document.body.classList.remove("selection-active");
  items().forEach((element, position) => {
    const index = +element.dataset.i;
    element.tabIndex = position === 0 ? 0 : -1;
    element.addEventListener("focus", () => (current.focus = index));
    const box = element.querySelector("input[data-choix]");
    if (box)
      box.addEventListener("click", (event) => {
        event.stopPropagation();
        if (event.shiftKey && current.anchor != null) {
          const [start, end] = [Math.min(current.anchor, index), Math.max(current.anchor, index)];
          for (let i = start; i <= end; i++) setSelected(i, true);
        } else setSelected(index, box.checked);
        current.anchor = index;
        refresh();
      });
    element.addEventListener(
      "click",
      (event) => {
        if (event.target.closest("input[data-choix], .btn-menu")) return;
        if (element.dataset.appuiLong) {
          delete element.dataset.appuiLong;
          event.preventDefault();
          event.stopPropagation();
          return;
        }
        const selecting = event.metaKey || event.ctrlKey || event.shiftKey || current.selected.size > 0;
        if (!selecting) return;
        event.preventDefault();
        event.stopPropagation();
        if (event.shiftKey && current.anchor != null) {
          const [start, end] = [Math.min(current.anchor, index), Math.max(current.anchor, index)];
          for (let i = start; i <= end; i++) setSelected(i, true);
        } else setSelected(index, !current.selected.has(index));
        current.anchor = index;
        refresh();
      },
      true,
    );
    element.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      openMenu(index);
    });
    let timer = null;
    element.addEventListener(
      "touchstart",
      () => {
        timer = setTimeout(() => {
          element.dataset.appuiLong = "1";
          setSelected(index, !current.selected.has(index));
          current.anchor = index;
          refresh();
          if (navigator.vibrate) navigator.vibrate(10);
        }, LONG_PRESS_MS);
      },
      { passive: true },
    );
    ["touchend", "touchmove", "touchcancel"].forEach((name) =>
      element.addEventListener(name, () => clearTimeout(timer), { passive: true }),
    );
    const menuButton = element.querySelector(".btn-menu");
    if (menuButton) menuButton.addEventListener("click", () => openMenu(index));
  });
  return current;
}
