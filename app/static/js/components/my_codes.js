// « Mes codes », dans Système (super admin) : tous les codes qui existent autour du serveur, à quoi
// ils servent et où ils sont rangés. Jabba ne montre jamais un code ici : seulement quoi en faire.
// « Fiche à imprimer » : les codes à garder, avec une ligne vide pour chacun, à remplir à la main et à
// ranger chez soi (une copie qui ne dépend ni du serveur, ni du trousseau).
//
// Sommaire :
//   partagé : myCodesCard
//   interne : codeHtml, sheetHtml, printSheet, GROUPS
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { state } from "../core/state.js";

const GROUPS = [
  ["trousseau", t("À garder dans ton trousseau")],
  ["jabba", t("Gardés par Jabba : rien à retenir")],
  ["supprimer", t("Tu peux les supprimer de ton trousseau")],
];

function codeHtml(code) {
  const missing = code.arrangement !== "supprimer" && !code.present;
  return `<li class="${missing ? "code-absent" : ""}"><strong>${escapeHtml(code.name)}</strong>${missing ? ` <span class="pastille p-gris">${escapeHtml(t("pas encore"))}</span>` : ""}
    <br><span class="note">${escapeHtml(code.purpose)}${code.note ? " " + escapeHtml(code.note) : ""}</span></li>`;
}

function sheetHtml(codes) {
  const today = new Date().toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
  const toKeep = codes.filter((code) => code.arrangement === "trousseau");
  return t`<h1>Codes du serveur ${escapeHtml((state.me && state.me.server) || location.hostname.split(".")[0])}</h1>
    <p>Fiche du ${escapeHtml(today)}. Remplis-la à la main, puis range-la dans une enveloppe, chez toi, loin du serveur.
      Elle sert le jour où tu n'as plus accès à ton trousseau.</p>
    <p><strong>Adresse de Jabba</strong> (avec Tailscale allumé) : ${escapeHtml(location.origin)}</p>
    <ol>${toKeep
      .map(
        (code) =>
          `<li><strong>${escapeHtml(code.name)}</strong><br><span>${escapeHtml(code.purpose)}</span><div class="ligne-a-remplir"></div></li>`,
      )
      .join("")}
      <li><strong>${escapeHtml(t("Compte Tailscale"))}</strong><br><span>${escapeHtml(t("L'adresse mail et la façon de te connecter (Apple, Google…) : sans lui, plus d'accès à distance."))}</span><div class="ligne-a-remplir"></div></li>
    </ol>
    <p>${escapeHtml(t("Jabba n'écrit jamais un code sur cette fiche : c'est toi qui les recopies."))}</p>`;
}

function printSheet(codes) {
  let sheet = document.getElementById("fiche-codes");
  if (!sheet) {
    sheet = document.createElement("section");
    sheet.id = "fiche-codes";
    document.body.appendChild(sheet);
  }
  sheet.innerHTML = sheetHtml(codes);
  document.body.classList.add("impression-codes");
  window.addEventListener("afterprint", () => document.body.classList.remove("impression-codes"), { once: true });
  window.print();
}

export async function myCodesCard() {
  const card = $("#mes-codes");
  if (!card) return;
  const { codes } = await api("/api/codes");
  card.innerHTML = t`<div class="entete-carte"><h2>Mes codes</h2><button class="btn btn-petit" type="button" data-imprimer>Fiche à imprimer</button></div><p class="note">Tous les codes autour de ton serveur, et où ils sont rangés. Jabba ne les affiche jamais ici. Un code qui n'est pas dans cette liste ne vient pas de Jabba : dans le doute, garde-le.</p>
    ${GROUPS.map(([place, title]) => {
      const items = codes.filter((code) => code.arrangement === place);
      return items.length
        ? `<h3 class="titre-codes">${escapeHtml(title)}</h3><ul class="liste-codes">${items.map(codeHtml).join("")}</ul>`
        : "";
    }).join("")}`;
  card.querySelector("[data-imprimer]").onclick = () => printSheet(codes);
}
