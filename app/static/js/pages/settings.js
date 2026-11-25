// Écran Réglages : tout ce qui se règle, rangé en rubriques (Moi, Sécurité, Alertes, Serveur, Internet).
// L'état du serveur (allumé depuis, courant, mise à jour, journal, appareils) reste dans Système.
// Rubrique dans l'adresse : #/reglages?r=alertes (les liens des autres écrans y mènent directement).
//
// Sommaire :
//   partagé : pages.reglages (écran)
//   interne : SECTIONS, currentSection, sectionsHtml, loadCards, loadServerTools
import { alertsCard } from "../components/alerts.js";
import { appearanceCard } from "../components/appearance_card.js";
import { addressCardHtml, bindAddresses } from "../components/address_card.js";
import { bindDomain, domainCardHtml } from "../components/domain_card.js";
import { toast } from "../components/dialogs.js";
import { heartbeatCard } from "../components/heartbeat_card.js";
import { myCodesCard } from "../components/my_codes.js";
import { publicModeCard } from "../components/public_mode_card.js";
import { restartPolicyCard } from "../components/restart_policy_card.js";
import { secondFactorCard } from "../components/second_factor.js";
import { bindSecurityCard, securityCardHtml } from "../components/security_card.js";
import { serverMoveCard } from "../components/server_move_card.js";
import { toolCardHtml } from "../components/tool_card.js";
import { bindToolMenus, runToolAction } from "../components/tool_menu.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatBytes, formatWhen } from "../core/format.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { state } from "../core/state.js";
import { loadingHtml } from "../core/tasks.js";
import { t } from "../core/i18n.js";

const SECTIONS = [
  ["moi", t("Moi")],
  ["securite", t("Sécurité")],
  ["alertes", t("Alertes")],
  ["serveur", t("Serveur")],
  ["internet", t("Internet")],
];
let currentSection = "moi";

pages.reglages = async (params = new URLSearchParams()) => {
  content.innerHTML = t`<div class="entete"><div><h1>Réglages</h1></div></div>${loadingHtml()}`;
  const entry = await api("/api/admin/etat");
  const asked = params.get("r");
  if (SECTIONS.some(([key]) => key === asked)) currentSection = asked;
  content.innerHTML = t`<div class="entete"><div><h1>Réglages</h1><p class="sous-titre">Ce qui se règle, rangé par rubrique. L'état du serveur est dans Système.</p></div></div>
    <nav class="sous-onglets" role="tablist">${SECTIONS.map(
      ([key, name]) =>
        `<button type="button" role="tab" data-onglet-btn="${key}" aria-selected="${key === currentSection}">${name}</button>`,
    ).join("")}</nav>
    ${sectionsHtml(entry)}`;
  const showSection = (key) => {
    currentSection = key;
    content.querySelectorAll("[data-onglet]").forEach((element) => (element.hidden = element.dataset.onglet !== key));
    content
      .querySelectorAll("[data-onglet-btn]")
      .forEach((element) => element.setAttribute("aria-selected", String(element.dataset.ongletBtn === key)));
    history.replaceState(null, "", `#/reglages?r=${key}`);
  };
  content
    .querySelectorAll("[data-onglet-btn]")
    .forEach((element) => element.addEventListener("click", () => showSection(element.dataset.ongletBtn)));
  showSection(currentSection);
  const reload = () => pages.reglages().catch(showError);
  bindSecurityCard(entry, reload);
  loadCards();
  bindDomain();
  // Une adresse d'outil manque : la carte de réparation s'affiche dans Internet.
  bindAddresses(reload);
  loadServerTools(reload).catch(() => {});
};

function sectionsHtml(entry) {
  const superAdmin = state.admin && state.admin.rank === "super";
  const card = (id, title) => `<section class="carte pad" id="${id}"><h2>${title}</h2>${loadingHtml()}</section>`;
  return t`
    <div class="grille g2" data-onglet="moi">
      <section class="carte pad" id="apparence"><h2>Apparence</h2></section>
      ${card("double", t("Double vérification"))}
    </div>
    <div class="grille g2" data-onglet="securite">
      ${securityCardHtml(entry)}
      ${superAdmin ? card("mes-codes", t("Mes codes")) : ""}
    </div>
    <div class="grille g2" data-onglet="alertes">
      ${card("alertes", t("Alertes"))}
      ${superAdmin ? card("surveillance", t("Si le serveur ne répond plus")) : ""}
    </div>
    <div data-onglet="serveur">
      <div class="grille g2">
        ${card("redem", t("Redémarrage après une mise à jour"))}
        ${card("public", t("Serveur ouvert au public"))}
        ${superAdmin ? card("demenagement", t("Déménager vers un nouvel ordinateur")) : ""}
      </div>
      <h2 class="titre-section" style="margin-top:24px">Outils du serveur</h2>
      <div class="grille g2" id="outils-serveur"></div>
      <p class="note" id="versions-outils"></p>
    </div>
    <div data-onglet="internet">
      ${domainCardHtml()}
      <div class="grille g2">${addressCardHtml()}</div>
    </div>`;
}

/** Chaque carte se charge seule ; une erreur ne s'affiche que dans sa carte. */
function loadCards() {
  appearanceCard();
  const cards = [
    [secondFactorCard, "double", t("Double vérification")],
    [myCodesCard, "mes-codes", t("Mes codes")],
    [alertsCard, "alertes", t("Alertes")],
    [heartbeatCard, "surveillance", t("Si le serveur ne répond plus")],
    [restartPolicyCard, "redem", t("Redémarrage après une mise à jour")],
    [publicModeCard, "public", t("Serveur ouvert au public")],
    [serverMoveCard, "demenagement", t("Déménager vers un nouvel ordinateur")],
  ];
  cards.forEach(([loader, id, title]) => {
    if (!$(`#${id}`)) return;
    loader().catch((error) => {
      const element = $(`#${id}`);
      if (element) element.innerHTML = `<h2>${title}</h2><p class="note">${escapeHtml(error.message)}</p>`;
    });
  });
}

/** Jabba lui-même et ses outils à lui (adresses…) : leurs cartes, leur menu, leur consommation. */
async function loadServerTools(reload) {
  const [tools, versions] = await Promise.all([
    api("/api/outils"),
    api("/api/outils-versions").catch(() => ({ tools: {}, read: 0 })),
  ]);
  const grid = $("#outils-serveur");
  if (!grid) return;
  const system = tools.filter((item) => item.system);
  const updateAvailable = (pid) => Boolean(versions.tools[pid] && !versions.tools[pid].up_to_date);
  grid.innerHTML = system.map((item) => toolCardHtml(item, updateAvailable(item.id))).join("");
  const lastCheck = versions.read
    ? t` (dernier passage : ${escapeHtml(formatWhen(new Date(versions.read * 1000).toISOString()))})`
    : "";
  $("#versions-outils").innerHTML =
    t`Jabba regarde chaque jour si une version plus récente de chaque outil existe${lastCheck} et t'alerte. <button class="lien" type="button" id="verifier-versions">Vérifier maintenant</button>`;
  $("#verifier-versions").onclick = async (event) => {
    event.target.disabled = true;
    try {
      await api("/api/outils-versions/verifier", { method: "POST" });
    } catch (error) {
      toast(error.message, true);
    }
    reload();
  };
  bindToolMenus(grid, system, versions, reload);
  grid.querySelectorAll("[data-action]").forEach((element) =>
    element.addEventListener("click", () =>
      runToolAction(
        system.find((item) => item.id === element.dataset.id),
        element.dataset.action,
        reload,
      ),
    ),
  );
  api("/api/outils-conso")
    .then((usage) => {
      grid.querySelectorAll("[data-conso]").forEach((element) => {
        const measured = usage[element.dataset.conso];
        if (measured)
          element.textContent = t`Mémoire ${formatBytes(measured.memory)} · processeur ${String(measured.cpu).replace(".", ",")} %`;
      });
    })
    .catch(() => {});
}
