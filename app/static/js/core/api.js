// Appels au serveur (api) et petits formulaires dans une fenêtre (formBody).
//
// Sommaire :
//   partagé : api, formBody
//   interne : reloadIfOutdated
import { dialogForm, dialogElement } from "../components/dialogs.js";
import { chooseMyPassword, confirmPassword, adminLogin } from "./admin_session.js";
import { escapeHtml } from "./dom.js";
import { t } from "./i18n.js";

// ------------------------------------------------------------ outils
// La version de l'interface chargée dans cette fenêtre (dans l'adresse des scripts : /static/v0.16.4/js/…).
const LOADED_VERSION = (import.meta.url.match(/\/static\/v([^/]+)\//) || [])[1] || null;

// Après une mise à jour, une fenêtre restée ouverte parle à un serveur plus récent que son interface :
// elle se recharge, une fois, à la lecture suivante (jamais au milieu d'un envoi : rien de tapé ne se perd).
function reloadIfOutdated(result, method) {
  const serverVersion = result.headers.get("X-Jabba-Version");
  if (!LOADED_VERSION || !serverVersion || serverVersion === LOADED_VERSION || method !== "GET") return false;
  try {
    if (sessionStorage.getItem("jabba-recharge") === serverVersion) return false; // déjà tenté
    sessionStorage.setItem("jabba-recharge", serverVersion);
  } catch {
    return false;
  }
  location.reload();
  return true;
}

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
    throw Object.assign(new Error(t("Le serveur ne répond pas.")), { unreachable: "reseau" });
  }
  if (reloadIfOutdated(result, (item.method || "GET").toUpperCase())) return new Promise(() => {});
  if (result.status >= 502 && result.status <= 504)
    throw Object.assign(new Error(t("L'appli Jabba ne répond pas sur le serveur.")), { unreachable: "appli" });
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
    throw new Error(t("Action annulée : mot de passe non saisi."));
  }
  if (!result.ok) {
    const data = body && (body.detail || body.error);
    const message = (data && (data.message || (typeof data === "string" ? data : null))) || t`Erreur ${result.status}`;
    throw Object.assign(new Error(message), { status: result.status });
  }
  return body;
}

// Formulaire dans une fenêtre : renvoie les valeurs, null (annulé) ou le nom du bouton secondaire.
export function formBody({ title, text = "", fields, confirmLabel = t("Valider"), secondary = null }) {
  return new Promise((resolve) => {
    dialogForm.innerHTML = t`<h2>${escapeHtml(title)}</h2>${text ? `<p>${escapeHtml(text)}</p>` : ""}
      ${fields
        .map((item) =>
          item.cache
            ? `<input type="text" name="${item.name}" value="${escapeHtml(item.value || "")}" autocomplete="${item.autocomplete || "off"}" hidden>`
            : item.options
              ? `<label class="champ"><span>${escapeHtml(item.label)}</span><select name="${item.name}">${item.options.map((entry) => `<option value="${escapeHtml(entry[0])}"${entry[0] === item.value ? " selected" : ""}>${escapeHtml(entry[1])}</option>`).join("")}</select></label>`
              : `<label class="champ"><span>${escapeHtml(item.label)}</span><input type="${item.type || "text"}" name="${item.name}" value="${escapeHtml(item.value || "")}" placeholder="${escapeHtml(item.hint || "")}" autocomplete="${item.autocomplete || "off"}" autocapitalize="off" spellcheck="false"></label>`,
        )
        .join("")}
      ${secondary ? `<button class="lien" value="secondary" type="submit">${escapeHtml(secondary)}</button>` : ""}
      <div class="actions"><button class="btn" value="non" type="submit" formnovalidate>Annuler</button>
      <button class="btn btn-p" value="oui" type="submit">${escapeHtml(confirmLabel)}</button></div>`;
    dialogElement.onclose = () => {
      dialogForm.onkeydown = null;
      if (dialogElement.returnValue === "secondary") return resolve("secondary");
      if (dialogElement.returnValue !== "oui") return resolve(null);
      const value = {};
      fields.forEach((item) => (value[item.name] = dialogForm.elements.namedItem(item.name).value));
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
