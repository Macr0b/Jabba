// Écran Établi : les outils du serveur (démarrer, arrêter, mettre à jour) et leur ouverture dans l'appli.
//
// Sommaire :
//   partagé : pages.outils (écran), pages.ouvrir (écran)
import { confirmAction, toast } from "../components/dialogs.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { antivirusOfferHtml, bindAntivirus } from "../components/antivirus_card.js";
import { formatBytes, formatWhen } from "../core/format.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { loadingHtml, followTask } from "../core/tasks.js";
import { COMING_SOON, toolIcon } from "./home.js";
import { t } from "../core/i18n.js";

pages.outils = async () => {
  content.innerHTML = t`<div class="entete"><div><h1>Établi</h1></div></div>${loadingHtml()}`;
  const [tools, versions] = await Promise.all([
    api("/api/outils"),
    api("/api/outils-versions").catch(() => ({ outils: {}, lu: 0 })),
  ]);
  const badge = { actif: "p-ok", partiel: "p-alerte", "en panne": "p-rouge", arrêté: "p-gris" };
  const workbenchTools = tools.filter((item) => !item.systeme),
    system = tools.filter((item) => item.systeme);
  const versionText = (pid) => {
    const known = versions.outils[pid];
    return known && known.version ? ` · version ${escapeHtml(known.version)}` : "";
  };
  const updateAvailable = (pid) => Boolean(versions.outils[pid] && !versions.outils[pid].a_jour);
  const card = (item) => {
    const active = item.etat !== "arrêté";
    return `
      <article class="carte pad outil${active ? "" : " eteint"}">
        <div class="haut">
          <div class="titre-outil">${toolIcon(item)}<div><h2>${escapeHtml(item.nom)}</h2><div class="role">${escapeHtml(item.role)}${versionText(item.id)}</div>${active ? `<div class="conso note" data-conso="${escapeHtml(item.id)}"></div>` : ""}</div></div>
          ${
            item.systeme || !item.modifiable
              ? `<span class="pastille ${badge[item.etat] || "p-gris"}">${escapeHtml(item.etat[0].toUpperCase() + item.etat.slice(1))}</span>`
              : `<button class="interrupteur" role="switch" aria-checked="${active}" aria-label="${active ? t("Désactiver") : t("Activer")} ${escapeHtml(item.nom)}" data-bascule="${escapeHtml(item.id)}"><span></span></button>`
          }
        </div>
        ${updateAvailable(item.id) ? t`<div class="bandeau"><span>Une version plus récente est disponible.</span></div>` : ""}
        ${item.etat === "en panne" || item.etat === "partiel" ? `<div class="note" style="color:var(--rouge)">${item.etat === "en panne" ? t("En panne : essaie Redémarrer.") : t("Une partie de l'outil ne tourne pas : essaie Redémarrer.")}</div>` : ""}
        ${
          active && !item.systeme
            ? `<div class="actions">
          ${
            item.lien
              ? item.dans_appli
                ? t`<a class="btn btn-p" href="#/ouvrir?outil=${encodeURIComponent(item.id)}">Ouvrir</a>`
                : item.id === "nextcloud"
                  ? t`<a class="btn btn-p" href="#/fichiers">Mes fichiers</a><a class="btn" href="#/nuage">Gérer<span class="compteur" id="attente-nuage" hidden></span></a><a class="btn" href="${escapeHtml(item.lien)}" target="_blank" rel="noopener">Nextcloud complet</a>`
                  : t`<a class="btn btn-p" href="${escapeHtml(item.lien)}" target="_blank" rel="noopener">Ouvrir</a>`
              : ""
          }
          ${
            item.modifiable
              ? t`<button class="btn" data-action="redemarrer" data-id="${escapeHtml(item.id)}">Redémarrer</button>
          <button class="btn${updateAvailable(item.id) ? " btn-p" : ""}" data-action="mettre-a-jour" data-id="${escapeHtml(item.id)}">Mettre à jour</button>`
              : ""
          }
        </div>`
            : ""
        }
        ${item.id === "antivirus" && active ? '<div id="antivirus-detail"></div>' : ""}
        ${item.systeme && item.modifiable ? t`<div class="actions"><button class="btn" data-action="mettre-a-jour" data-id="${escapeHtml(item.id)}">Mettre à jour</button></div>` : ""}
      </article>`;
  };
  content.innerHTML = t`
    <div class="entete"><div><h1>Établi</h1><p class="sous-titre">Active les outils dont tu as besoin. Un outil désactivé ne consomme rien et garde ses données.</p></div></div>
    <div class="grille g2">${workbenchTools.map(card).join("")}</div>
    ${workbenchTools.some((item) => item.id === "antivirus") ? "" : antivirusOfferHtml()}
    <h2 class="titre-section">Bientôt dans l'établi</h2>
    <div class="grille g2">${COMING_SOON.map((item) => t`<article class="carte pad outil eteint"><div class="haut"><div class="titre-outil">${toolIcon({ nom: item.nom })}<div><h2>${escapeHtml(item.nom)}</h2><div class="role">${escapeHtml(item.role)}</div></div></div><span class="pastille p-gris">Bientôt</span></div></article>`).join("")}</div>
    <h2 class="titre-section">Système</h2>
    <div class="grille g2">${system.map(card).join("")}</div>
    <p class="note">« Mettre à jour » télécharge la dernière version de l'outil et le relance (1 à 5 minutes). Tes réglages et tes fichiers sont conservés.
      Jabba regarde chaque jour si une version plus récente existe${versions.lu ? t` (dernier passage : ${escapeHtml(formatWhen(new Date(versions.lu * 1000).toISOString()))})` : ""} et t'alerte. <button class="lien" type="button" id="verifier-versions">Vérifier maintenant</button></p>`;
  // Consommation de chaque outil : arrive un peu après (docker stats prend 1 à 2 secondes).
  api("/api/outils-conso")
    .then((usage) => {
      content.querySelectorAll("[data-conso]").forEach((element) => {
        const measured = usage[element.dataset.conso];
        if (!measured) return;
        element.textContent = t`Mémoire ${formatBytes(measured.memoire)} · processeur ${String(measured.processeur).replace(".", ",")} %`;
      });
    })
    .catch(() => {});
  bindAntivirus(() => pages.outils().catch(showError));
  $("#verifier-versions").onclick = async (event) => {
    event.target.disabled = true;
    try {
      await api("/api/outils-versions/verifier", { method: "POST" });
    } catch (error) {
      toast(error.message, true);
    }
    pages.outils().catch(showError);
  };

  const reload = () => {
    if (location.hash.startsWith("#/outils")) pages.outils().catch(showError);
  };
  // Inscriptions Nextcloud en attente : une pastille sur « Gérer ».
  if ($("#attente-nuage"))
    api("/api/nuage/attente")
      .then((result) => {
        const element = $("#attente-nuage");
        if (element && result.attente) {
          element.textContent = result.attente;
          element.hidden = false;
        }
      })
      .catch(() => {});
  content.querySelectorAll("[data-bascule]").forEach((element) =>
    element.addEventListener("click", async () => {
      const item = tools.find((item) => item.id === element.dataset.bascule);
      const enable = element.getAttribute("aria-checked") !== "true";
      if (
        !enable &&
        !(await confirmAction({
          titre: t`Désactiver ${item.nom} ?`,
          texte: t(
            "Il ne sera plus accessible, sur aucun appareil, jusqu'à ce que tu le réactives. Ses réglages et ses données sont conservés.",
          ),
          valider: t("Désactiver"),
          danger: true,
        }))
      )
        return;
      element.setAttribute("aria-checked", String(enable));
      content.querySelectorAll("[data-bascule],[data-action]").forEach((item) => (item.disabled = true));
      try {
        await followTask(
          await api(`/api/outils/${encodeURIComponent(item.id)}/${enable ? "demarrer" : "arreter"}`, {
            method: "POST",
          }),
        );
      } catch (error) {
        toast(error.message, true);
      }
      reload();
    }),
  );
  content.querySelectorAll("[data-action]").forEach((element) =>
    element.addEventListener("click", async () => {
      const item = tools.find((item) => item.id === element.dataset.id);
      const texts = {
        redemarrer: ["Redémarrer " + item.nom + " ?", t("Il sera indisponible quelques secondes."), t("Redémarrer")],
        "mettre-a-jour": [
          "Mettre à jour " + item.nom + " ?",
          t("Il sera indisponible pendant la mise à jour (1 à 5 minutes)."),
          t("Mettre à jour"),
        ],
      }[element.dataset.action];
      if (!(await confirmAction({ titre: texts[0], texte: texts[1], valider: texts[2] }))) return;
      content.querySelectorAll("[data-bascule],[data-action]").forEach((item) => (item.disabled = true));
      try {
        await followTask(
          await api(`/api/outils/${encodeURIComponent(item.id)}/${element.dataset.action}`, { method: "POST" }),
        );
      } catch (error) {
        toast(error.message, true);
      }
      reload();
    }),
  );
};

// Ouvrir un outil dans l'appli, sans passer par le navigateur.
pages.ouvrir = async (params) => {
  const [tools, versions] = await Promise.all([
    api("/api/outils"),
    api("/api/outils-versions").catch(() => ({ outils: {}, lu: 0 })),
  ]);
  const item = tools.find((item) => item.id === params.get("outil"));
  if (!item || !item.lien) throw new Error(t("Outil introuvable ou désactivé."));
  document.body.classList.add("plein-ecran");
  content.innerHTML = t`
    <div class="barre-outil">
      <a class="btn btn-petit" href="#/outils">← Établi</a>
      <span class="titre-outil">${toolIcon(item, 24)}<strong>${escapeHtml(item.nom)}</strong></span>
      <a class="btn btn-petit" href="${escapeHtml(item.lien)}" target="_blank" rel="noopener">Navigateur ↗</a>
    </div>
    <iframe class="cadre-outil" src="${escapeHtml(item.lien)}" title="${escapeHtml(item.nom)}" allow="fullscreen; autoplay; clipboard-write" allowfullscreen></iframe>
    <p class="note aide-cadre">Page blanche ? Utilise « Navigateur ↗ » en haut à droite.</p>`;
};
