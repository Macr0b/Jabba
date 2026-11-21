// « Mes codes », dans Système (super admin) : tous les codes qui existent autour du serveur, à quoi
// ils servent et où ils sont rangés. Jabba ne montre jamais un code ici : seulement quoi en faire.
//
// Sommaire :
//   partagé : myCodesCard
//   interne : codeHtml, GROUPS
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";

const GROUPS = [
  ["trousseau", t("À garder dans ton trousseau")],
  ["jabba", t("Gardés par Jabba : rien à retenir")],
  ["supprimer", t("Tu peux les supprimer de ton trousseau")],
];

function codeHtml(code) {
  const missing = code.rangement !== "supprimer" && !code.present;
  return `<li class="${missing ? "code-absent" : ""}"><strong>${escapeHtml(code.nom)}</strong>${missing ? ` <span class="pastille p-gris">${escapeHtml(t("pas encore"))}</span>` : ""}
    <br><span class="note">${escapeHtml(code.sert_a)}${code.note ? " " + escapeHtml(code.note) : ""}</span></li>`;
}

export async function myCodesCard() {
  const card = $("#mes-codes");
  if (!card) return;
  const { codes } = await api("/api/codes");
  card.innerHTML = t`<h2>Mes codes</h2><p class="note">Tous les codes autour de ton serveur, et où ils sont rangés. Jabba ne les affiche jamais ici. Un code qui n'est pas dans cette liste ne vient pas de Jabba : dans le doute, garde-le.</p>
    ${GROUPS.map(([place, title]) => {
      const items = codes.filter((code) => code.rangement === place);
      return items.length
        ? `<h3 class="titre-codes">${escapeHtml(title)}</h3><ul class="liste-codes">${items.map(codeHtml).join("")}</ul>`
        : "";
    }).join("")}`;
}
