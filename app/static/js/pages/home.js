// Écran Accueil : accès rapide, favoris, dossiers suggérés, outils.
//
// Sommaire :
//   partagé : pages.accueil (écran), folderLink, toggleFavorite, toolIcon, COMING_SOON
//   interne : TILE_ICONS, loadQuickAccess
import { toast } from "../components/dialogs.js";
import { enableDrop } from "../components/drop_targets.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatDuration, formatBytes, formatPercent } from "../core/format.js";
import { pages } from "../core/pages.js";
import { state } from "../core/state.js";
import { loadingHtml } from "../core/tasks.js";
import { t } from "../core/i18n.js";

pages.accueil = async () => {
  content.innerHTML = t`<div class="entete"><div><h1>Bonjour</h1><p class="sous-titre">Ton serveur</p></div></div>${loadingHtml()}`;
  const data = await api("/api/etat");
  const ok = !data.soucis.length;
  content.innerHTML = t`
    <div class="entete">
      <div><h1>${escapeHtml(state.me && state.me.serveur ? state.me.serveur[0].toUpperCase() + state.me.serveur.slice(1) : t("Le serveur"))}</h1><p class="sous-titre">Allumé depuis ${formatDuration(data.duree)} · ${data.outils_actifs} outil${data.outils_actifs > 1 ? "s" : ""} sur ${data.outils_total} actif${data.outils_actifs > 1 ? "s" : ""}</p></div>
      <span class="pastille ${ok ? "p-ok" : "p-alerte"}">${ok ? t("Tout va bien") : t("À vérifier")}</span>
    </div>
    ${data.soucis.length ? t`<a class="bandeau" href="#/outils"><span><strong>Arrêté ou en panne :</strong> ${escapeHtml(data.soucis.join(", "))}</span><strong>Voir</strong></a>` : ""}
    ${data.redemarrage_requis ? t`<a class="bandeau" href="#/systeme"><span>Une mise à jour attend un <strong>redémarrage</strong> (${data.redemarrage_auto === false ? t("redémarre quand tu es devant") : t("il se fera seul à 4 h")}).</span><strong>Voir</strong></a>` : ""}
    <div class="grille g4">
      <div class="carte pad mesure"><div class="etiq">Processeur</div><div class="val">${data.cpu} %</div><div class="jauge"><span style="width:${data.cpu}%"></span></div></div>
      <div class="carte pad mesure"><div class="etiq">Mémoire</div><div class="val">${formatBytes(data.memoire.utilise)}</div><div class="detail">sur ${formatBytes(data.memoire.total)}</div></div>
      <div class="carte pad mesure"><div class="etiq">Température</div><div class="val">${data.temperature ?? "—"} °C</div><div class="detail">${data.temperature == null ? "" : data.temperature < 70 ? t("Normale") : data.temperature < 85 ? t("Chaude") : t("Trop chaude")}</div></div>
      <div class="carte pad mesure"><div class="etiq">Disque de données</div><div class="val">${formatPercent(data.donnees)} %</div><div class="detail">${formatBytes(data.donnees?.utilise)} sur ${formatBytes(data.donnees?.total)}</div><div class="jauge"><span style="width:${formatPercent(data.donnees)}%"></span></div></div>
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
  loadQuickAccess().catch((error) => {
    const element = $("#tuiles");
    if (element) element.innerHTML = `<p class="note">${escapeHtml(error.message)}</p>`;
  });
};

export const folderLink = (item) => "#/fichiers?chemin=" + encodeURIComponent(item);
const TILE_ICONS = { racine: "dossier", espace: t("dossier ext"), disque: "disque", favori: "favori" };

async function loadQuickAccess() {
  const data = await api("/api/accueil/dossiers");
  const tiles = $("#tuiles"),
    suggestions = $("#suggestions");
  if (!tiles) return;
  const tile = (item, type) =>
    `<a class="carte tuile" href="${folderLink(item.chemin)}"><span class="ic-tuile ${TILE_ICONS[type]}" aria-hidden="true"></span><span class="nom-tuile">${escapeHtml(item.nom)}</span><span class="note">${{ racine: t("Nextcloud"), espace: t("Serveur"), disque: t("Disque branché"), favori: t("Favori") }[type]}</span></a>`;
  tiles.innerHTML =
    data.essentiels.map((item) => tile(item, item.type)).join("") +
    data.favoris.map((value) => tile(value, "favori")).join("") +
    (data.favoris.length
      ? ""
      : t`<div class="tuile vide-tuile note">Ajoute un dossier ici avec l'étoile ☆ de l'écran Fichiers.</div>`);
  tiles
    .querySelectorAll("a.tuile")
    .forEach((item) =>
      enableDrop(item, decodeURIComponent(item.getAttribute("href").split("chemin=")[1] || ""), () => {}),
    );
  suggestions.innerHTML = data.suggestions
    .map(
      (item) => t`
    <div class="bandeau suggestion"><span>Tu ouvres souvent <strong>${escapeHtml(item.nom)}</strong>. L'ajouter à l'accès rapide ?</span>
      <span class="actions"><button class="btn btn-petit btn-p" data-fav="${escapeHtml(item.chemin)}">Ajouter</button><button class="btn btn-petit" data-ign="${escapeHtml(item.chemin)}">Non merci</button></span></div>`,
    )
    .join("");
  suggestions.querySelectorAll("[data-fav],[data-ign]").forEach((element) =>
    element.addEventListener("click", async () => {
      const path = element.dataset.fav || element.dataset.ign;
      try {
        await api("/api/favoris", {
          method: "POST",
          json: { chemin: path, action: element.dataset.fav ? "ajouter" : "ignorer" },
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
  await api("/api/favoris", { method: "POST", json: { chemin: path, action: favorite ? "retirer" : "ajouter" } });
  toast(favorite ? t("Retiré de l'accès rapide") : t("Ajouté à l'accès rapide"));
}

// Icône d'un outil : le logo servi par l'outil lui-même, sinon son initiale.
export function toolIcon(item, size = 40) {
  const initial = escapeHtml((item.nom || "?")[0].toUpperCase());
  if (!item.icone) return `<span class="icone" style="width:${size}px;height:${size}px">${initial}</span>`;
  return `<span class="icone" style="width:${size}px;height:${size}px"><img alt="" src="/api/icone/${encodeURIComponent(item.id)}" data-initiale="${initial}"></span>`;
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

export const COMING_SOON = [
  { nom: t("Torrent derrière VPN"), role: t("Téléchargements, avec un abonnement VPN") },
  { nom: t("Sauvegarde croisée"), role: t("Une copie chiffrée sur un autre serveur Jabba") },
];
