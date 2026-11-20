// « Ranger » l'Établi : déplacer les cartes (glisser, ou flèches ↑ ↓ au doigt et au clavier), ordre gardé sur le serveur.
//
// Sommaire :
//   partagé : applyOrder, bindOrdering
//   interne : cardId, cards, moveControlsHtml, save
import { toast } from "./dialogs.js";
import { api } from "../core/api.js";
import { escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";

const CARD = "[data-carte], [data-offre], [data-integre]";

function cardId(card) {
  return card.dataset.carte || card.dataset.offre || card.dataset.integre;
}

function cards(grid) {
  return [...grid.querySelectorAll(`:scope > ${CARD.split(", ").join(", :scope > ")}`)];
}

/** Remet les cartes dans l'ordre enregistré ; celles qui n'y sont pas gardent leur place, à la suite. */
export function applyOrder(grid, order) {
  const rank = new Map(order.map((id, index) => [id, index]));
  cards(grid)
    .map((card, index) => ({ card, key: rank.has(cardId(card)) ? rank.get(cardId(card)) : order.length + index }))
    .sort((a, b) => a.key - b.key)
    .forEach(({ card }) => grid.appendChild(card));
}

function moveControlsHtml(card) {
  const name = card.querySelector("h2")?.textContent || "";
  return `<div class="deplacer-carte">
      <button class="btn btn-petit" type="button" data-monter aria-label="${escapeHtml(t`Monter ${name}`)}">↑</button>
      <button class="btn btn-petit" type="button" data-descendre aria-label="${escapeHtml(t`Descendre ${name}`)}">↓</button>
    </div>`;
}

async function save(grid) {
  try {
    await api("/api/etabli/ordre", { method: "PUT", json: { ordre: cards(grid).map(cardId) } });
  } catch (error) {
    toast(error.message, true);
  }
}

/** Le bouton « Ranger » : passe la grille en mode rangement, « Terminé » enregistre. */
export function bindOrdering(button, grid) {
  let dragged = null;
  button.onclick = async () => {
    const active = grid.classList.toggle("rangement");
    button.textContent = active ? t("Terminé") : t("Ranger");
    button.setAttribute("aria-pressed", String(active));
    cards(grid).forEach((card) => {
      card.querySelector(".deplacer-carte")?.remove();
      card.draggable = active;
      if (active) card.insertAdjacentHTML("afterbegin", moveControlsHtml(card));
    });
    if (!active) await save(grid);
  };
  grid.addEventListener("click", (event) => {
    const up = event.target.closest("[data-monter]");
    const down = event.target.closest("[data-descendre]");
    if (!up && !down) return;
    const card = event.target.closest(CARD);
    const sibling = up ? card.previousElementSibling : card.nextElementSibling;
    if (!sibling) return;
    if (up) grid.insertBefore(card, sibling);
    else grid.insertBefore(sibling, card);
    event.target.closest("button").focus();
  });
  grid.addEventListener("dragstart", (event) => {
    if (!grid.classList.contains("rangement")) return;
    dragged = event.target.closest(CARD);
  });
  grid.addEventListener("dragover", (event) => {
    if (!dragged) return;
    event.preventDefault();
    const over = event.target.closest(CARD);
    if (!over || over === dragged) return;
    const after = [...grid.children].indexOf(over) > [...grid.children].indexOf(dragged);
    grid.insertBefore(dragged, after ? over.nextElementSibling : over);
  });
  grid.addEventListener("dragend", () => {
    dragged = null;
  });
}
