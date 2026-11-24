// Établi, « Mes outils » : la carte d'un outil installé. Le curseur le met en marche ou en pause (en pause,
// il ne consomme rien et garde tout) ; un seul bouton, « Ouvrir » ; « Mettre à jour » apparaît seulement
// quand une version plus récente existe. Le reste (Redémarrer, Identifiants, Aide, Désinstaller…) est dans
// le menu « ••• » (tool_menu.js).
//
// Sommaire :
//   partagé : toolCardHtml, statusHtml
//   interne : openButtonsHtml, toolHint, STATUS
import { escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { toolIcon } from "../pages/home.js";
import { toolMenuHtml } from "./tool_menu.js";

// L'état d'un outil, en clair : [classe de la pastille, texte].
const STATUS = {
  actif: ["p-ok", t("En marche")],
  partiel: ["p-alerte", t("En partie arrêté")],
  "en panne": ["p-rouge", t("En panne")],
  arrêté: ["p-gris", t("En pause · ne consomme rien")],
};

export function statusHtml(item) {
  const [badge, text] = STATUS[item.state] || ["p-gris", item.state];
  return `<span class="pastille ${badge}">${escapeHtml(text)}</span>`;
}

/** Comment se servir d'un outil qui n'a pas de page à ouvrir. */
function toolHint(item) {
  if (item.id !== "time-machine") return "";
  return t`<p class="note">Sur le Mac : Réglages Système → Général → Time Machine → Ajouter un disque de sauvegarde → <strong>TimeMachine</strong>, compte <strong>timemachine</strong> et le mot de passe de ••• → Identifiants. Les sauvegardes vont dans le dossier TimeMachine du disque de données.</p>`;
}

/** « Ouvrir » (dans l'appli si l'outil l'accepte) ; Nextcloud a ses fichiers et sa gestion des comptes. */
function openButtonsHtml(item) {
  if (item.page) return t`<a class="btn btn-p" href="${escapeHtml(item.page)}">Ouvrir</a>`;
  if (!item.link) return "";
  if (item.id === "nextcloud")
    return t`<a class="btn btn-p" href="#/fichiers">Mes fichiers</a><a class="btn" href="#/nuage">Comptes<span class="compteur" id="attente-nuage" hidden></span></a><a class="btn" href="${escapeHtml(item.link)}" target="_blank" rel="noopener">Nextcloud ↗</a>`;
  if (item.in_app) return t`<a class="btn btn-p" href="#/ouvrir?outil=${encodeURIComponent(item.id)}">Ouvrir</a>`;
  return t`<a class="btn btn-p" href="${escapeHtml(item.link)}" target="_blank" rel="noopener">Ouvrir</a>`;
}

/** `update` : une version plus récente existe. */
export function toolCardHtml(item, update = false) {
  const running = item.state !== "arrêté";
  const toggle =
    item.editable && !item.system
      ? `<button class="interrupteur" role="switch" aria-checked="${running}" aria-label="${escapeHtml(running ? t`Mettre ${item.name} en pause` : t`Remettre ${item.name} en marche`)}" data-bascule="${escapeHtml(item.id)}"><span></span></button>`
      : "";
  const trouble =
    item.state === "en panne" || item.state === "partiel"
      ? `<p class="note alerte-memoire">${escapeHtml(t("Essaie Redémarrer, dans le menu •••."))}</p>`
      : "";
  return `<article class="carte pad outil${running ? "" : " eteint"}" data-carte="${escapeHtml(item.id)}">
      <div class="haut">
        <div class="titre-outil">${toolIcon(item)}<div><h2>${escapeHtml(item.name)}</h2><div class="role">${escapeHtml(item.role)}</div></div></div>
        <div class="haut-droite">${toggle}${toolMenuHtml(item)}</div>
      </div>
      <div class="etat-outil">${statusHtml(item)}${update ? `<span class="pastille p-alerte">${escapeHtml(t("Mise à jour disponible"))}</span>` : ""}${running ? `<span class="note" data-conso="${escapeHtml(item.id)}"></span>` : ""}</div>
      ${trouble}
      ${
        running
          ? `<div class="actions">${openButtonsHtml(item)}${
              update
                ? t`<button class="btn" data-action="mettre-a-jour" data-id="${escapeHtml(item.id)}">Mettre à jour</button>`
                : ""
            }</div>${toolHint(item)}`
          : ""
      }
      ${item.id === "antivirus" && running ? '<div id="antivirus-detail"></div>' : ""}
      <div class="panneau-outil" data-panneau="${escapeHtml(item.id)}" hidden></div>
    </article>`;
}
