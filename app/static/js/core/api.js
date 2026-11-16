// Appels au serveur (api) et petits formulaires dans une fenêtre (formBody).
//
// Sommaire :
//   partagé : api, formBody
import { dialogForm, dialogElement } from "../components/dialogs.js";
import { chooseMyPassword, confirmPassword, adminLogin } from "./admin_session.js";
import { escapeHtml } from "./dom.js";

// ------------------------------------------------------------ outils
// La connexion admin est un cookie protégé, posé par le serveur et gardé 30 jours sur l'appareil.
export async function api(path, options = {}, secondAttempt = false) {
  const item = { credentials: "same-origin", ...options, headers: { "X-Jabba": "1", ...(options.headers || {}) } };
  if (item.json !== undefined) {
    item.body = JSON.stringify(item.json);
    item.headers["Content-Type"] = "application/json";
    delete item.json;
  }
  let result;
  try {
    result = await fetch(path, item);
  } catch (_) {
    throw Object.assign(new Error("Le serveur ne répond pas."), { injoignable: "reseau" });
  }
  if (result.status >= 502 && result.status <= 504)
    throw Object.assign(new Error("L'appli Jabba ne répond pas sur le serveur."), { injoignable: "appli" });
  let body = null;
  try {
    body = await result.json();
  } catch (_) {
    /* vide */
  }
  if (result.status === 401 && body && body.detail && body.detail.admin && !secondAttempt) {
    const data = body.detail;
    const ok = data.changer
      ? await chooseMyPassword(true)
      : data.confirmer
        ? await confirmPassword()
        : await adminLogin();
    if (ok) return api(path, options, true);
    throw new Error("Action annulée : mot de passe non saisi.");
  }
  if (!result.ok) {
    const data = body && (body.detail || body.erreur);
    throw new Error((data && (data.message || (typeof data === "string" ? data : null))) || `Erreur ${result.status}`);
  }
  return body;
}

// Formulaire dans une fenêtre : renvoie les valeurs, null (annulé) ou le nom du bouton secondaire.
export function formBody({
  titre: title,
  texte: text = "",
  champs: fields,
  valider: confirmLabel = "Valider",
  secondaire: secondary = null,
}) {
  return new Promise((resolve) => {
    dialogForm.innerHTML = `<h2>${escapeHtml(title)}</h2>${text ? `<p>${escapeHtml(text)}</p>` : ""}
      ${fields
        .map((item) =>
          item.cache
            ? `<input type="text" name="${item.nom}" value="${escapeHtml(item.valeur || "")}" autocomplete="${item.autocomplete || "off"}" hidden>`
            : item.options
              ? `<label class="champ"><span>${escapeHtml(item.label)}</span><select name="${item.nom}">${item.options.map((entry) => `<option value="${escapeHtml(entry[0])}"${entry[0] === item.valeur ? " selected" : ""}>${escapeHtml(entry[1])}</option>`).join("")}</select></label>`
              : `<label class="champ"><span>${escapeHtml(item.label)}</span><input type="${item.type || "text"}" name="${item.nom}" value="${escapeHtml(item.valeur || "")}" placeholder="${escapeHtml(item.indice || "")}" autocomplete="${item.autocomplete || "off"}" autocapitalize="off" spellcheck="false"></label>`,
        )
        .join("")}
      ${secondary ? `<button class="lien" value="secondaire" type="submit">${escapeHtml(secondary)}</button>` : ""}
      <div class="actions"><button class="btn" value="non" type="submit" formnovalidate>Annuler</button>
      <button class="btn btn-p" value="oui" type="submit">${escapeHtml(confirmLabel)}</button></div>`;
    dialogElement.onclose = () => {
      dialogForm.onkeydown = null;
      if (dialogElement.returnValue === "secondaire") return resolve("secondaire");
      if (dialogElement.returnValue !== "oui") return resolve(null);
      const value = {};
      fields.forEach((item) => (value[item.nom] = dialogForm[item.nom].value));
      resolve(value);
    };
    // Entrée dans un champ = le bouton principal (sinon le navigateur prend le premier bouton : « Mot de passe oublié ? »).
    dialogForm.onkeydown = (event) => {
      if (event.key === "Enter" && !event.isComposing && event.target.tagName === "INPUT") {
        event.preventDefault();
        dialogElement.close("oui");
      }
    };
    dialogElement.returnValue = "";
    dialogElement.showModal();
    const first =
      [...dialogForm.querySelectorAll("input:not([hidden])")].find((item) => !item.value) ||
      dialogForm.querySelector("input:not([hidden])");
    if (first) first.focus();
  });
}
