// Écran Établi : « Mes outils » (installés : en marche ou en pause, cartes de tool_card.js) et « Ajouter un
// outil » (tool_catalog.js). Jabba lui-même, l'accès depuis Internet et les adresses : écran Réglages.
// Puis l'ouverture d'un outil dans l'appli.
//
// Sommaire :
//   partagé : pages.outils (écran), pages.ouvrir (écran)
//   interne : toggleTool
import { confirmAction, toast } from "../components/dialogs.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { antivirusOfferHtml, bindAntivirus } from "../components/antivirus_card.js";
import { applyArrangement, bindOrdering, loadArrangement } from "../components/card_order.js";
import { bindCatalog, memoryBannerHtml } from "../components/tool_catalog.js";
import { toolCardHtml } from "../components/tool_card.js";
import { bindToolMenus, runToolAction } from "../components/tool_menu.js";
import { formatBytes, formatWhen } from "../core/format.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { loadingHtml, followTask } from "../core/tasks.js";
import { toolIcon } from "./home.js";
import { t } from "../core/i18n.js";

pages.outils = async () => {
  content.innerHTML = t`<div class="entete"><div><h1>Établi</h1></div></div>${loadingHtml()}`;
  const [tools, versions, savedOrder] = await Promise.all([
    api("/api/outils"),
    api("/api/outils-versions").catch(() => ({ tools: {}, read: 0 })),
    loadArrangement("etabli"),
  ]);
  // Un outil sans adresse à lui est publié sur Tailscale au même nom que Jabba, sur son propre port.
  tools.forEach((item) => {
    if (!item.link && item.port_https) item.link = `https://${location.hostname}:${item.port_https}`;
  });
  const mine = tools.filter((item) => !item.system);
  const updateAvailable = (pid) => Boolean(versions.tools[pid] && !versions.tools[pid].up_to_date);
  const lastCheck = versions.read
    ? t` (dernier passage : ${escapeHtml(formatWhen(new Date(versions.read * 1000).toISOString()))})`
    : "";
  content.innerHTML = t`
    <div class="entete"><div><div class="titre-avec-action"><h1>Établi</h1><button class="btn btn-petit" type="button" id="ranger-etabli" aria-pressed="false">Ranger</button></div><p class="sous-titre">Tes outils. Le curseur les met en pause : en pause, un outil ne consomme rien et garde tout.</p>${memoryBannerHtml()}</div></div>
    <h2 class="titre-section">Mes outils</h2>
    <div class="grille g2" id="grille-etabli">${mine.map((item) => toolCardHtml(item, updateAvailable(item.id))).join("")}</div>
    <h2 class="titre-section">Ajouter un outil</h2>
    <div id="ajouter-outils"></div>
    <p class="note">« Mettre à jour » télécharge la dernière version de l'outil et le relance (1 à 5 minutes). Tes réglages et tes fichiers sont conservés.
      Jabba regarde chaque jour si une version plus récente existe${lastCheck} et t'alerte. <button class="lien" type="button" id="verifier-versions">Vérifier maintenant</button></p>
    <p class="note">Jabba lui-même, l'accès depuis Internet et les adresses des outils : <a href="#/reglages?r=serveur">Réglages</a>.</p>`;
  // Consommation de chaque outil : arrive un peu après (docker stats prend 1 à 2 secondes).
  api("/api/outils-conso")
    .then((usage) => {
      content.querySelectorAll("[data-conso]").forEach((element) => {
        const measured = usage[element.dataset.conso];
        if (!measured) return;
        element.textContent = t`Mémoire ${formatBytes(measured.memory)} · processeur ${String(measured.cpu).replace(".", ",")} %`;
      });
    })
    .catch(() => {});
  const reloadPage = () => pages.outils().catch(showError);
  bindAntivirus(reloadPage);
  bindToolMenus(content, tools, versions, reloadPage);
  const grid = $("#grille-etabli");
  applyArrangement(grid, savedOrder, { pausedLast: true }); // en pause : après ceux qui tournent
  bindOrdering($("#ranger-etabli"), [{ grid, screen: "etabli" }]);
  const extras = mine.some((item) => item.id === "antivirus") ? {} : { antivirus: antivirusOfferHtml() };
  bindCatalog({ grid, offers: $("#ajouter-outils"), extras }, reloadPage, (catalogTools) => {
    bindToolMenus(content, catalogTools, versions, reloadPage);
    bindAntivirus(reloadPage); // l'offre de l'antivirus arrive avec les autres
    applyArrangement(grid, savedOrder, { pausedLast: true }); // les outils de l'appli arrivent après : ils prennent leur place aussi
  });
  $("#verifier-versions").onclick = async (event) => {
    event.target.disabled = true;
    try {
      await api("/api/outils-versions/verifier", { method: "POST" });
    } catch (error) {
      toast(error.message, true);
    }
    reloadPage();
  };
  // Inscriptions Nextcloud en attente : une pastille sur « Comptes ».
  if ($("#attente-nuage"))
    api("/api/nuage/attente")
      .then((result) => {
        const element = $("#attente-nuage");
        if (element && result.pending) {
          element.textContent = result.pending;
          element.hidden = false;
        }
      })
      .catch(() => {});
  content
    .querySelectorAll("[data-bascule]")
    .forEach((element) =>
      element.addEventListener("click", () => toggleTool(tools.find((item) => item.id === element.dataset.bascule))),
    );
  content.querySelectorAll("[data-action]").forEach((element) =>
    element.addEventListener("click", () =>
      runToolAction(
        tools.find((item) => item.id === element.dataset.id),
        element.dataset.action,
        reloadPage,
      ),
    ),
  );
};

/** Le curseur : en pause (après confirmation) ou de nouveau en marche. */
async function toggleTool(item) {
  const resume = item.state === "arrêté";
  if (
    !resume &&
    !(await confirmAction({
      title: t`Mettre ${item.name} en pause ?`,
      text: t(
        "Il ne sera plus accessible, sur aucun appareil, jusqu'à ce que tu le remettes en marche. Il ne consomme plus rien, et ses réglages et ses données sont gardés.",
      ),
      confirmLabel: t("Mettre en pause"),
    }))
  )
    return;
  content.querySelectorAll("[data-bascule],[data-action]").forEach((element) => (element.disabled = true));
  try {
    await followTask(
      await api(`/api/outils/${encodeURIComponent(item.id)}/${resume ? "demarrer" : "arreter"}`, { method: "POST" }),
    );
  } catch (error) {
    toast(error.message, true);
  }
  if (location.hash.startsWith("#/outils")) pages.outils().catch(showError);
}

// Ouvrir un outil dans l'appli, sans passer par le navigateur.
pages.ouvrir = async (params) => {
  const [tools, versions] = await Promise.all([
    api("/api/outils"),
    api("/api/outils-versions").catch(() => ({ tools: {}, read: 0 })),
  ]);
  const item = tools.find((item) => item.id === params.get("outil"));
  if (item && !item.link && item.port_https) item.link = `https://${location.hostname}:${item.port_https}`;
  if (!item || !item.link) throw new Error(t("Outil introuvable ou désactivé."));
  document.body.classList.add("plein-ecran");
  content.innerHTML = t`
    <div class="barre-outil">
      <a class="btn btn-petit" href="#/outils">← Établi</a>
      <span class="titre-outil">${toolIcon(item, 24)}<strong>${escapeHtml(item.name)}</strong></span>
      <a class="btn btn-petit" href="${escapeHtml(item.link)}" target="_blank" rel="noopener">Navigateur ↗</a>
    </div>
    <iframe class="cadre-outil" src="${escapeHtml(item.link)}" title="${escapeHtml(item.name)}" allow="fullscreen; autoplay; clipboard-write" allowfullscreen></iframe>
    <p class="note aide-cadre">Page blanche ? Utilise « Navigateur ↗ » en haut à droite.</p>`;
};
