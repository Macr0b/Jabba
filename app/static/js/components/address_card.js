// Adresses des outils, dans l'Établi : la carte de réparation. Normalement, chaque outil qui a un compte
// reçoit son adresse à part (paperless.tailxxxx.ts.net…) pendant son activation, avec la clé Tailscale
// donnée au script d'installation (services/tool_addresses.py). La carte ne s'affiche que s'il en manque
// une : serveur installé avant la 0.16.4, clé expirée, ou création ratée. Réservé au super admin.
//
// Sommaire :
//   partagé : addressCardHtml, bindAddresses
//   interne : detailHtml, toolLineHtml
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { state } from "../core/state.js";
import { toast } from "./dialogs.js";
import { toolIcon } from "../pages/home.js";

export function addressCardHtml() {
  if (!(state.admin && state.admin.rang === "super")) return "";
  return t`<article class="carte pad outil" id="carte-adresses" hidden>
      <div class="haut"><div class="titre-outil">${toolIcon({ nom: t("Adresses") })}<div><h2>Adresses des outils</h2><div class="role">Une adresse à part pour chaque outil : le trousseau ne mélange plus les mots de passe</div></div></div></div>
      <div id="adresses-detail"><p class="note">Chargement…</p></div>
    </article>`;
}

function toolLineHtml(tool) {
  const shown = tool.adresse
    ? `<a href="${escapeHtml(tool.adresse)}" target="_blank" rel="noopener">${escapeHtml(tool.adresse.replace("https://", ""))}</a>`
    : tool.etat === "en cours"
      ? `<span class="pastille p-gris">${escapeHtml(t("En cours…"))}</span>`
      : tool.etat
        ? `<span class="note alerte-memoire">${escapeHtml(tool.etat)}</span>`
        : `<span class="pastille p-gris">${escapeHtml(t("À créer"))}</span>`;
  return `<li><strong>${escapeHtml(tool.nom)}</strong> ${shown}</li>`;
}

function detailHtml(status) {
  const missing = status.outils.filter((tool) => !tool.adresse && tool.etat !== "en cours");
  const list = status.outils.length
    ? `<ul class="liste-adresses">${status.outils.map(toolLineHtml).join("")}</ul>`
    : t`<p class="note">Aucun outil avec un compte n'est allumé pour l'instant.</p>`;
  if (!missing.length) return list;
  if (status.cle_prete)
    return t`${list}<div class="actions"><button class="btn btn-p" type="button" id="adresses-creer">Créer les adresses</button><button class="lien" type="button" id="adresses-oublier">Oublier la clé</button></div>`;
  return t`${list}
    <ol class="note">
      <li>Ouvre la page des clés de Tailscale, puis <strong>Generate access token…</strong> Description : Jabba ; expiration : laisse la valeur proposée. <strong>Generate access token</strong>, puis <strong>Copy</strong>.</li>
      <li>Colle-la ici. Jabba crée les adresses (une minute environ par outil), puis garde la clé jusqu'à son expiration : les outils que tu activeras ensuite auront leur adresse sans rien te demander.</li>
    </ol>
    <p><a class="btn" href="${escapeHtml(status.page_cles)}" target="_blank" rel="noopener">Ouvrir la page des clés Tailscale</a></p>
    <label class="champ"><span>Clé d'accès (tskey-api-…)</span><input type="text" class="saisie-secrete" id="adresses-cle" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
    <div class="actions"><button class="btn btn-p" type="button" id="adresses-enregistrer">Créer les adresses</button></div>`;
}

/** `whenDone` : rappelé quand la création se termine (les boutons « Ouvrir » prennent les nouvelles adresses). */
export function bindAddresses(whenDone) {
  const detail = $("#adresses-detail");
  if (!detail) return;
  let timer = null;
  let wasBusy = false;
  const render = (status) => {
    if (!document.body.contains(detail)) return;
    const card = $("#carte-adresses");
    // Tout est en place : rien à réparer, la carte ne s'affiche pas.
    card.hidden = status.outils.every((tool) => tool.adresse);
    detail.innerHTML = detailHtml(status);
    clearTimeout(timer);
    // Pendant la création, la carte se met à jour toute seule.
    const busy = status.outils.some((tool) => tool.etat === "en cours");
    if (busy) timer = setTimeout(load, 4000);
    if (wasBusy && !busy && whenDone) return whenDone();
    wasBusy = busy;
    const call = async (button, path, json, done) => {
      button.disabled = true;
      try {
        render(await api(path, { method: "POST", json }));
        if (done) toast(done);
      } catch (error) {
        toast(error.message, true);
        button.disabled = false;
      }
    };
    const save = $("#adresses-enregistrer");
    if (save)
      save.onclick = () =>
        call(save, "/api/adresses/cle", { cle: $("#adresses-cle").value }, t("Création des adresses lancée."));
    const create = $("#adresses-creer");
    if (create) create.onclick = () => call(create, "/api/adresses/creer", {}, t("Création des adresses lancée."));
    const forget = $("#adresses-oublier");
    if (forget) forget.onclick = () => call(forget, "/api/adresses/oublier-cle", {}, t("Clé Tailscale oubliée."));
  };
  const load = () =>
    api("/api/adresses")
      .then(render)
      .catch((error) => {
        detail.innerHTML = `<p class="note alerte-memoire">${escapeHtml(error.message)}</p>`;
      });
  load();
}
