// Sélecteur de dossier : pour « Déplacer vers… », on navigue dans les dossiers au lieu de taper un chemin.
//
// Sommaire :
//   partagé : pickFolder
//   interne : parentOf, render
import { api } from "../core/api.js";
import { escapeHtml } from "../core/dom.js";
import { dialogElement, dialogForm, toast } from "./dialogs.js";
import { t } from "../core/i18n.js";

const parentOf = (path) => path.replace(/\/[^/]*$/, "") || "/";

// Renvoie le chemin choisi, ou null. `excluded` : dossiers où l'on ne peut pas aller (ceux qu'on déplace).
export function pickFolder({ title, start = "/", confirmLabel = t("Déplacer ici"), excluded = [] }) {
  return new Promise((resolve) => {
    let current = start;
    let chosen = null;
    const blocked = (path) => excluded.some((item) => path === item || path.startsWith(item + "/"));

    async function render() {
      let listing;
      try {
        listing = await api("/api/fichiers?visite=0&chemin=" + encodeURIComponent(current));
      } catch (error) {
        toast(error.message, true);
        if (current !== "/") {
          current = parentOf(current);
          return render();
        }
        return dialogElement.close("non");
      }
      const folders = listing.items.filter((entry) => entry.folder && !blocked(entry.path));
      const writable = listing.writable && current !== "/" && !blocked(current);
      dialogForm.innerHTML = t`<h2>${escapeHtml(title)}</h2>
        <p class="chemin-choisi"><span class="note">Dans :</span> <strong>${escapeHtml(current === "/" ? t("Tous les emplacements") : current)}</strong></p>
        <div class="liste-dossiers" role="list">
          ${current !== "/" ? t('<button class="ligne-dossier" type="button" data-aller=".." role="listitem">↑ Dossier parent</button>') : ""}
          ${folders.map((entry) => `<button class="ligne-dossier" type="button" data-aller="${escapeHtml(entry.path)}" role="listitem"><span class="ic-dossier" aria-hidden="true"></span>${escapeHtml(entry.name)}<span aria-hidden="true">›</span></button>`).join("")}
          ${folders.length ? "" : t('<p class="note">Aucun sous-dossier.</p>')}
        </div>
        ${writable ? "" : `<p class="note">${current === "/" ? t("Ouvre un dossier pour y déplacer.") : t("Ce dossier est en lecture seule.")}</p>`}
        <div class="actions"><button class="btn" value="non" type="submit" formnovalidate>Annuler</button>
        <button class="btn btn-p" value="oui" type="submit"${writable ? "" : " disabled"}>${escapeHtml(confirmLabel)}</button></div>`;
      dialogForm.querySelectorAll("[data-aller]").forEach((button) => {
        button.onclick = () => {
          current = button.dataset.aller === ".." ? parentOf(current) : button.dataset.aller;
          render();
        };
      });
      const first = dialogForm.querySelector("[data-aller]");
      if (first) first.focus();
    }

    dialogForm.onsubmit = (event) => {
      if (event.submitter && event.submitter.value === "oui") chosen = current;
    };
    dialogElement.onclose = () => {
      dialogForm.onsubmit = null;
      resolve(chosen);
    };
    dialogElement.returnValue = "";
    dialogElement.showModal();
    render();
  });
}
