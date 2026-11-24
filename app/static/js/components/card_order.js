// « Ranger » un écran : déplacer les cartes (glisser, ou flèches ↑ ↓ au doigt et au clavier) et en masquer.
// Le rangement est gardé sur le serveur, par écran, le même sur tous les appareils.
// Une carte masquée disparaît ; pendant le rangement, elle reste visible (grisée) pour la remettre.
//
// Sommaire :
//   partagé : loadArrangement, applyArrangement, bindOrdering
//   interne : cardId, cards, controlsHtml, save
import { toast } from "./dialogs.js";
import { api } from "../core/api.js";
import { escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";

const CARD = "[data-carte], [data-offre], [data-integre]";

function cardId(card) {
  return card.dataset.carte || card.dataset.offre || card.dataset.integre;
}

function cards(grid) {
  return [...grid.children].filter((child) => child.matches(CARD));
}

/** Le rangement enregistré d'un écran (« etabli », « accueil-mesures », « accueil-tuiles »). */
export function loadArrangement(screen) {
  return api(`/api/rangement/${screen}`).catch(() => ({ order: [], hidden_items: [] }));
}

/**
 * Remet les cartes dans l'ordre enregistré et cache les masquées ; les autres gardent leur place, à la suite.
 * `pausedLast` (Établi) : les outils en pause passent après ceux en marche, chacun dans l'ordre rangé.
 */
export function applyArrangement(grid, arrangement, { pausedLast = false } = {}) {
  const rank = new Map(arrangement.order.map((id, index) => [id, index]));
  const paused = (card) => (pausedLast && card.classList.contains("eteint") ? 1 : 0);
  cards(grid)
    .map((card, index) => ({
      card,
      key: rank.has(cardId(card)) ? rank.get(cardId(card)) : arrangement.order.length + index,
    }))
    .sort((a, b) => paused(a.card) - paused(b.card) || a.key - b.key)
    .forEach(({ card }) => {
      card.classList.toggle("carte-masquee", arrangement.hidden_items.includes(cardId(card)));
      grid.appendChild(card);
    });
}

function controlsHtml(card) {
  const name = card.querySelector("h2, .etiq, .nom-tuile")?.textContent || "";
  const hidden = card.classList.contains("carte-masquee");
  return `<div class="deplacer-carte">
      <button class="btn btn-petit" type="button" data-monter aria-label="${escapeHtml(t`Monter ${name}`)}">↑</button>
      <button class="btn btn-petit" type="button" data-descendre aria-label="${escapeHtml(t`Descendre ${name}`)}">↓</button>
      <button class="btn btn-petit" type="button" data-masquer aria-pressed="${hidden}">${hidden ? escapeHtml(t("Afficher")) : escapeHtml(t("Masquer"))}</button>
    </div>`;
}

async function save(screen, grid) {
  const all = cards(grid);
  const body = {
    order: all.map(cardId),
    hidden_items: all.filter((card) => card.classList.contains("carte-masquee")).map(cardId),
  };
  try {
    await api(`/api/rangement/${screen}`, { method: "PUT", json: body });
  } catch (error) {
    toast(error.message, true);
  }
}

/**
 * Le bouton « Ranger » d'un écran : passe ses grilles en mode rangement ; « Terminé » enregistre.
 * `grids` : [{ grid, screen }] — une ou plusieurs grilles rangées ensemble (l'Accueil en a deux).
 */
export function bindOrdering(button, grids) {
  let dragged = null;
  button.onclick = async () => {
    const active = button.getAttribute("aria-pressed") !== "true";
    button.textContent = active ? t("Terminé") : t("Ranger");
    button.setAttribute("aria-pressed", String(active));
    for (const { grid, screen } of grids) {
      grid.classList.toggle("rangement", active);
      cards(grid).forEach((card) => {
        card.querySelector(".deplacer-carte")?.remove();
        card.draggable = active;
        if (active) card.insertAdjacentHTML("afterbegin", controlsHtml(card));
      });
      if (!active) await save(screen, grid);
    }
  };
  for (const { grid } of grids) {
    grid.addEventListener("click", (event) => {
      const control = event.target.closest("[data-monter], [data-descendre], [data-masquer]");
      if (!control || !grid.classList.contains("rangement")) return;
      event.preventDefault(); // une tuile est un lien : pas de navigation pendant le rangement
      const card = control.closest(CARD);
      if (control.hasAttribute("data-masquer")) {
        const hidden = card.classList.toggle("carte-masquee");
        control.setAttribute("aria-pressed", String(hidden));
        control.textContent = hidden ? t("Afficher") : t("Masquer");
        return;
      }
      const up = control.hasAttribute("data-monter");
      const sibling = up ? card.previousElementSibling : card.nextElementSibling;
      if (!sibling || !sibling.matches(CARD)) return;
      if (up) grid.insertBefore(card, sibling);
      else grid.insertBefore(sibling, card);
      control.focus();
    });
    grid.addEventListener("dragstart", (event) => {
      if (grid.classList.contains("rangement")) dragged = event.target.closest(CARD);
    });
    grid.addEventListener("dragover", (event) => {
      if (!dragged || dragged.parentElement !== grid) return;
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
}
