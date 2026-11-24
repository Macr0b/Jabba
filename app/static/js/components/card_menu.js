// Menu « ••• » d'une carte : une petite liste qui s'ouvre sous le bouton (pas de fenêtre par-dessus).
// Se ferme en cliquant ailleurs, avec Échap, ou après un choix ; au clavier, flèches haut et bas.
//
// Sommaire :
//   partagé : menuHtml, bindMenus
//   interne : closeAll

/** Le bouton et sa liste. `items` : [{ action, texte, danger? }]. */
export function menuHtml(id, label, items) {
  const entries = items
    .map(
      (item) =>
        `<li role="none"><button type="button" role="menuitem" class="${item.danger ? "danger" : ""}" data-menu-action="${item.action}">${item.text}</button></li>`,
    )
    .join("");
  return `<span class="menu-carte">
      <button class="btn-menu" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="${label}" data-menu="${id}">•••</button>
      <ul class="liste-menu" role="menu" hidden>${entries}</ul></span>`;
}

function closeAll(except) {
  document.querySelectorAll(".menu-carte .liste-menu:not([hidden])").forEach((list) => {
    if (list === except) return;
    list.hidden = true;
    list.previousElementSibling.setAttribute("aria-expanded", "false");
  });
}

let documentBound = false;

/** `onChoice(action, id)` est appelé avec l'action choisie et l'identifiant de la carte. */
export function bindMenus(scope, onChoice) {
  if (!documentBound) {
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".menu-carte")) closeAll();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeAll();
    });
    documentBound = true;
  }
  scope.querySelectorAll("[data-menu]").forEach((button) => {
    if (button.dataset.lie) return;
    button.dataset.lie = "1";
    const list = button.nextElementSibling;
    const items = () => [...list.querySelectorAll("[role=menuitem]")];
    button.addEventListener("click", () => {
      const opening = list.hidden;
      closeAll(list);
      list.hidden = !opening;
      button.setAttribute("aria-expanded", String(opening));
      if (opening) items()[0].focus();
    });
    list.addEventListener("keydown", (event) => {
      const all = items();
      const index = all.indexOf(document.activeElement);
      if (event.key === "ArrowDown") all[(index + 1) % all.length].focus();
      else if (event.key === "ArrowUp") all[(index - 1 + all.length) % all.length].focus();
      else return;
      event.preventDefault();
    });
    list.querySelectorAll("[data-menu-action]").forEach((item) => {
      item.addEventListener("click", () => {
        closeAll();
        button.focus();
        onChoice(item.dataset.menuAction, button.dataset.menu);
      });
    });
  });
}
