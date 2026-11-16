// Messages en bas de l'écran (toast) et fenêtres de dialogue (confirmer, choisir dans un menu).
//
// Sommaire :
//   partagé : toast, dialogElement, dialogForm, confirmAction, chooseFromMenu
import { $, escapeHtml } from "../core/dom.js";

export function toast(text, isError = false) {
  const element = document.createElement("div");
  element.className = "toast" + (isError ? " err" : "");
  element.textContent = text;
  $("#toasts").appendChild(element);
  setTimeout(() => element.remove(), isError ? 7000 : 3500);
}

export const dialogElement = $("#dialogue"),
  dialogForm = $("#dialogue-form");
export function confirmAction({ titre: title, text = "", field = null, confirmLabel = "OK", danger = false }) {
  return new Promise((resolve) => {
    dialogForm.innerHTML = `<h2>${escapeHtml(title)}</h2>${text ? `<p>${escapeHtml(text)}</p>` : ""}
      ${field !== null ? `<input type="text" name="champ" value="${escapeHtml(field)}" autocomplete="off" autocapitalize="off">` : ""}
      <div class="actions"><button class="btn" value="non" type="submit">Annuler</button>
      <button class="btn ${danger ? "btn-danger" : "btn-p"}" value="oui" type="submit">${escapeHtml(confirmLabel)}</button></div>`;
    dialogElement.onclose = () => {
      const ok = dialogElement.returnValue === "oui";
      const value = field !== null ? dialogForm.champ.value.trim() : true;
      resolve(ok ? value : null);
    };
    dialogElement.showModal();
    if (field !== null) {
      const item = dialogForm.champ;
      item.focus();
      const value = item.value.lastIndexOf(".");
      item.setSelectionRange(0, value > 0 ? value : item.value.length);
    }
  });
}
export function chooseFromMenu(title, choices) {
  return new Promise((resolve) => {
    dialogForm.innerHTML = `<h2>${escapeHtml(title)}</h2><div class="menu-actions">${choices
      .map(
        (item, index) =>
          `<button class="btn ${item.danger ? "btn-danger" : ""}" value="${index}" type="submit">${escapeHtml(item.texte)}</button>`,
      )
      .join("")}</div><div class="actions"><button class="btn" value="" type="submit">Fermer</button></div>`;
    dialogElement.onclose = () =>
      resolve(dialogElement.returnValue === "" ? null : choices[+dialogElement.returnValue]);
    dialogElement.returnValue = "";
    dialogElement.showModal();
  });
}
