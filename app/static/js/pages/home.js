// Écran Accueil : accès rapide, favoris, dossiers suggérés, outils.
//
// Sommaire :
//   partagé : pages.accueil (écran), folderLink, toggleFavorite, toolIcon
//   interne : TILE_ICONS, loadQuickAccess
import { applyArrangement, bindOrdering, loadArrangement } from "../components/card_order.js";
import { toast } from "../components/dialogs.js";
import { enableDrop } from "../components/drop_targets.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatDuration, formatBytes, formatPercent } from "../core/format.js";
import { pages } from "../core/pages.js";
import { state } from "../core/state.js";
import { loadingHtml } from "../core/tasks.js";
import { icon, toolIconName } from "../core/icons.js";
import { t } from "../core/i18n.js";

pages.accueil = async () => {
  content.innerHTML = t`<div class="entete"><div><h1>Bonjour</h1><p class="sous-titre">Ton serveur</p></div></div>${loadingHtml()}`;
  const data = await api("/api/etat");
  const ok = !data.issues.length;
  content.innerHTML = t`
    <div class="entete">
      <div><div class="titre-avec-action"><h1>${escapeHtml(state.me && state.me.server ? state.me.server[0].toUpperCase() + state.me.server.slice(1) : t("Le serveur"))}</h1><button class="btn btn-petit" type="button" id="ranger-accueil" aria-pressed="false">Ranger</button></div><p class="sous-titre">Allumé depuis ${formatDuration(data.duration)} · ${data.active_tools} outil${data.active_tools > 1 ? "s" : ""} sur ${data.tools_total} actif${data.active_tools > 1 ? "s" : ""}</p></div>
      <span class="pastille ${ok ? "p-ok" : "p-alerte"}">${ok ? t("Tout va bien") : t("À vérifier")}</span>
    </div>
    ${data.issues.length ? t`<a class="bandeau" href="#/outils"><span><strong>Arrêté ou en panne :</strong> ${escapeHtml(data.issues.join(", "))}</span><strong>Voir</strong></a>` : ""}
    ${data.redemarrage_requis ? t`<a class="bandeau" href="#/systeme"><span>Une mise à jour attend un <strong>redémarrage</strong> (${data.auto_restart === false ? t("redémarre quand tu es devant") : t("il se fera seul à 4 h")}).</span><strong>Voir</strong></a>` : ""}
    <div class="grille g4" id="mesures-accueil">
      <div class="carte pad mesure" data-carte="processeur"><div class="etiq">Processeur</div><div class="val">${data.cpu} %</div><div class="jauge"><span style="width:${data.cpu}%"></span></div></div>
      <div class="carte pad mesure" data-carte="memoire"><div class="etiq">Mémoire</div><div class="val">${formatBytes(data.memory.used)}</div><div class="detail">sur ${formatBytes(data.memory.total)}</div></div>
      <div class="carte pad mesure" data-carte="temperature"><div class="etiq">Température</div><div class="val">${data.temperature ?? "—"} °C</div><div class="detail">${data.temperature == null ? "" : data.temperature < 70 ? t("Normale") : data.temperature < 85 ? t("Chaude") : t("Trop chaude")}</div></div>
      <div class="carte pad mesure" data-carte="donnees"><div class="etiq">Disque de données</div><div class="val">${formatPercent(data.data)} %</div><div class="detail">${formatBytes(data.data?.used)} sur ${formatBytes(data.data?.total)}</div><div class="jauge"><span style="width:${formatPercent(data.data)}%"></span></div></div>
    </div>
    <section aria-label="Accès rapide">
      <h2 class="titre-section">Accès rapide</h2>
      <div id="suggestions"></div>
      <div class="tuiles" id="tuiles"><div class="squelette" style="height:72px"></div></div>
    </section>
    <section class="carte liste-raccourcis" aria-label="Outils">
      <a href="#/ouvrir?outil=jellyfin"><span>Regarder un film</span><span>Jellyfin</span></a>
      <a href="#/outils"><span>Activer ou gérer les outils</span><span>Établi</span></a>
      <a href="#/disques"><span>Disques et espace</span><span>Disques</span></a>
      <a href="#/systeme"><span>Système et appareils</span><span>Système</span></a>
    </section>`;
  const [measures, tiles] = await Promise.all([loadArrangement("accueil-mesures"), loadArrangement("accueil-tuiles")]);
  applyArrangement($("#mesures-accueil"), measures);
  bindOrdering($("#ranger-accueil"), [
    { grid: $("#mesures-accueil"), screen: "accueil-mesures" },
    { grid: $("#tuiles"), screen: "accueil-tuiles" },
  ]);
  loadQuickAccess(tiles).catch((error) => {
    const element = $("#tuiles");
    if (element) element.innerHTML = `<p class="note">${escapeHtml(error.message)}</p>`;
  });
};

export const folderLink = (item) => "#/fichiers?chemin=" + encodeURIComponent(item);
const TILE_ICONS = { racine: "dossier", espace: t("dossier ext"), disque: "disque", favori: "favori" };

async function loadQuickAccess(arrangement = { order: [], hidden_items: [] }) {
  const data = await api("/api/accueil/dossiers");
  const tiles = $("#tuiles");
  const suggestions = $("#suggestions");
  if (!tiles) return;
  const tile = (item, type) =>
    `<a class="carte tuile" href="${folderLink(item.path)}" data-carte="${escapeHtml(item.path)}"><span class="ic-tuile ${TILE_ICONS[type]}" aria-hidden="true"></span><span class="nom-tuile">${escapeHtml(item.name)}</span><span class="note">${{ racine: t("Nextcloud"), espace: t("Serveur"), disque: t("Disque branché"), favori: t("Favori") }[type]}</span></a>`;
  tiles.innerHTML =
    data.essentials.map((item) => tile(item, item.type)).join("") +
    data.favorites.map((value) => tile(value, "favori")).join("") +
    (data.favorites.length
      ? ""
      : t`<div class="tuile vide-tuile note">Ajoute un dossier ici avec l'étoile ☆ de l'écran Fichiers.</div>`);
  applyArrangement(tiles, arrangement);
  tiles
    .querySelectorAll("a.tuile")
    .forEach((item) =>
      enableDrop(item, decodeURIComponent(item.getAttribute("href").split("chemin=")[1] || ""), () => {}),
    );
  suggestions.innerHTML = data.suggestions
    .map(
      (item) => t`
    <div class="bandeau suggestion"><span>Tu ouvres souvent <strong>${escapeHtml(item.name)}</strong>. L'ajouter à l'accès rapide ?</span>
      <span class="actions"><button class="btn btn-petit btn-p" data-fav="${escapeHtml(item.path)}">Ajouter</button><button class="btn btn-petit" data-ign="${escapeHtml(item.path)}">Non merci</button></span></div>`,
    )
    .join("");
  suggestions.querySelectorAll("[data-fav],[data-ign]").forEach((element) =>
    element.addEventListener("click", async () => {
      const path = element.dataset.fav || element.dataset.ign;
      try {
        await api("/api/favoris", {
          method: "POST",
          json: { path, action: element.dataset.fav ? "ajouter" : "ignorer" },
        });
        if (element.dataset.fav) toast(t("Ajouté à l'accès rapide"));
      } catch (error) {
        toast(error.message, true);
      }
      loadQuickAccess().catch(() => {});
    }),
  );
}

// Fait d'un élément (tuile, ligne de dossier) une cible de dépôt de fichiers.
export async function toggleFavorite(path, favorite) {
  await api("/api/favoris", { method: "POST", json: { path, action: favorite ? "retirer" : "ajouter" } });
  toast(favorite ? t("Retiré de l'accès rapide") : t("Ajouté à l'accès rapide"));
}

// Icône d'un outil : le logo servi par l'outil lui-même, sinon son initiale.
export function toolIcon(item, size = 40) {
  // Une icône dessinée pour les outils connus ; sinon le logo de l'outil, ou son initiale.
  const drawn = toolIconName(item.id);
  const box = `class="icone" style="width:${size}px;height:${size}px"`;
  if (drawn) return `<span ${box}>${icon(drawn, Math.round(size * 0.5))}</span>`;
  const initial = escapeHtml((item.name || "?")[0].toUpperCase());
  if (!item.icon) return `<span ${box}>${initial}</span>`;
  return `<span ${box}><img alt="" src="/api/icone/${encodeURIComponent(item.id)}" data-initiale="${initial}"></span>`;
}

// Logo introuvable : on remet l'initiale. Écouté ici plutôt qu'avec onerror="…",
// que la politique de sécurité de la page bloque.
document.addEventListener(
  "error",
  (event) => {
    const image = event.target;
    if (image.tagName === "IMG" && image.dataset.initiale && image.parentNode)
      image.parentNode.textContent = image.dataset.initiale;
  },
  true,
);
