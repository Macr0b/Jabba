// Écran Fichiers : parcourir, chercher, envoyer, déplacer, supprimer (la Corbeille : pages/trash.js).
//
// Sommaire :
//   partagé : pages.fichiers (écran)
//   interne : openEntry, readViewPreferences, saveViewPreferences, sortEntries, rowHtml, tileHtml, searchBarHtml, wireSearch, showSearchResults, filesHistory, rememberFolder, goInHistory, navigationBarHtml, locationsCache, readLocations, locationPickerHtml
import { confirmAction, dialogElement, toast } from "../components/dialogs.js";
import { attachSelection, selectAll, SHORTCUTS } from "../components/file_selection.js";
import { publicModeOpen } from "../components/public_mode_card.js";
import { enableDrop } from "../components/drop_targets.js";
import { droppedItems, pickedItems, uploadItems } from "../components/folder_upload.js";
import { kindOf, entryIcon, downloadUrl, openViewer } from "../components/viewer.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatBytes, formatWhen } from "../core/format.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { loadingHtml } from "../core/tasks.js";
import { toggleFavorite, folderLink } from "./home.js";
import { t } from "../core/i18n.js";

function openEntry(list, index) {
  const entry = list[index];
  if (entry.dossier) location.hash = folderLink(entry.chemin);
  else if (kindOf(entry.nom)) openViewer(list, index);
  else location.href = downloadUrl(entry.chemin);
}

const searchBarHtml = (query = "") => t`<form class="recherche" id="form-recherche" role="search">
    <input type="search" name="q" value="${escapeHtml(query)}" placeholder="Chercher un fichier ou un dossier, partout" aria-label="Chercher" autocomplete="off" enterkeyhint="search">
  </form>`;
function wireSearch() {
  const element = $("#form-recherche");
  if (element)
    element.onsubmit = (event) => {
      event.preventDefault();
      const query = element.q.value.trim();
      if (query.length >= 2) location.hash = "#/fichiers?q=" + encodeURIComponent(query);
    };
}

async function showSearchResults(query) {
  content.innerHTML = t`<div class="entete"><div><h1>Recherche</h1><p class="sous-titre">« ${escapeHtml(query)} »</p></div></div>${searchBarHtml(query)}${loadingHtml(4)}`;
  wireSearch();
  const data = await api("/api/recherche?q=" + encodeURIComponent(query));
  const element = data.elements;
  content.innerHTML = t`
    <div class="entete"><div><h1>Recherche</h1><p class="sous-titre">${element.length} résultat${element.length > 1 ? "s" : ""} pour « ${escapeHtml(query)} »</p></div>
      <div class="actions"><a class="btn" href="#/fichiers">← Fichiers</a></div></div>
    ${searchBarHtml(query)}
    <section class="carte tableau" aria-label="Résultats">
      ${
        element.length
          ? element
              .map(
                (item, index) => `
        <div class="ligne">
          <span class="nom">${entryIcon(item)}
            <span><a href="#" data-ouvrir="${index}">${escapeHtml(item.nom)}</a>
            <span class="detail-chemin"><a href="${folderLink(item.chemin.split("/").slice(0, -1).join("/") || "/")}">${escapeHtml(item.chemin.split("/").slice(0, -1).join("/") || "/")}</a></span></span></span>
          <span class="gris col-l">${item.dossier ? t("Dossier") : formatBytes(item.taille)}</span>
          <span class="gris col-l">${formatWhen(item.modifie)}</span>
          <span></span>
        </div>`,
              )
              .join("")
          : t`<div class="vide">Rien trouvé. Essaie un autre mot, ou une partie du nom.</div>`
      }
    </section>
    ${data.complet ? "" : t`<p class="note">La recherche s'est arrêtée avant la fin (trop de fichiers). Précise ta recherche.</p>`}`;
  wireSearch();
  content.querySelectorAll("[data-ouvrir]").forEach((node) =>
    node.addEventListener("click", (event) => {
      event.preventDefault();
      openEntry(element, +node.dataset.ouvrir);
    }),
  );
}

// Historique de navigation dans Fichiers : Précédent / Suivant, comme dans le Finder.
const filesHistory = { pile: [], pos: -1, saut: null };
function rememberFolder(path) {
  const history = filesHistory;
  if (history.saut !== null && history.pile[history.saut] === path) history.pos = history.saut;
  else if (history.pile[history.pos - 1] === path)
    history.pos -= 1; // retour du navigateur ou de la souris
  else if (history.pile[history.pos + 1] === path) history.pos += 1;
  else if (history.pile[history.pos] !== path) {
    history.pile = history.pile
      .slice(0, history.pos + 1)
      .concat(path)
      .slice(-100);
    history.pos = history.pile.length - 1;
  }
  history.saut = null;
}
function goInHistory(direction) {
  const history = filesHistory,
    target = history.pos + direction;
  if (target < 0 || target >= history.pile.length) return;
  history.saut = target;
  location.hash = "#/fichiers?chemin=" + encodeURIComponent(history.pile[target]);
}
function navigationBarHtml(path, link, root) {
  const history = filesHistory,
    parent = "/" + path.split("/").filter(Boolean).slice(0, -1).join("/");
  return t`<div class="nav-fichiers" role="group" aria-label="Navigation">
    <button class="btn btn-petit" id="nav-prec" title="Précédent (⌘[)" aria-label="Précédent"${history.pos > 0 ? "" : " disabled"}>‹</button>
    <button class="btn btn-petit" id="nav-suiv" title="Suivant (⌘])" aria-label="Suivant"${history.pos < history.pile.length - 1 ? "" : " disabled"}>›</button>
    ${
      path !== "/"
        ? t`<a class="btn btn-petit" href="${link(parent)}" title="Dossier parent (⌘↑)">↑ Dossier parent</a>
    <a class="btn btn-petit" href="${link("/")}" title="Revenir au début">⌂ ${escapeHtml(root)}</a>`
        : ""
    }
  </div>`;
}
document.addEventListener("keydown", (event) => {
  if (
    !location.hash.startsWith("#/fichiers") ||
    location.hash.includes("q=") ||
    dialogElement.open ||
    /INPUT|TEXTAREA|SELECT/.test(event.target.tagName)
  )
    return;
  const command = event.metaKey || event.ctrlKey;
  if ((command && event.key === "[") || (event.altKey && event.key === "ArrowLeft")) {
    event.preventDefault();
    goInHistory(-1);
  } else if ((command && event.key === "]") || (event.altKey && event.key === "ArrowRight")) {
    event.preventDefault();
    goInHistory(1);
  } else if (command && event.key === "ArrowUp") {
    event.preventDefault();
    const hours = $("#nav-fichiers-parent");
    if (hours) location.hash = hours.getAttribute("href");
  }
});

// Emplacements où naviguer : Mes fichiers, puis chaque espace et chaque disque branché.
const locationsCache = { t: 0, v: null };
async function readLocations() {
  if (locationsCache.v && Date.now() - locationsCache.t < 30000) return locationsCache.v;
  try {
    const data = await api("/api/accueil/dossiers");
    locationsCache.v = data.essentiels;
    locationsCache.t = Date.now();
  } catch (_) {
    locationsCache.v = locationsCache.v || [];
  }
  return locationsCache.v;
}
function locationPickerHtml(list, path, link) {
  if (!list || list.length < 2) return "";
  const active = [...list].filter(
    (item) => item.chemin !== "/" && (path === item.chemin || path.startsWith(item.chemin + "/")),
  )[0];
  return t`<nav class="emplacements" aria-label="Emplacement">${list
    .map((item) => {
      const selected = active ? active === item : item.chemin === "/";
      const icon = item.type === "disque" ? "ic-disque" : item.type === "racine" ? "ic-maison" : "ic-dossier";
      return `<a class="puce-emplacement${selected ? " actif" : ""}" href="${link(item.chemin)}"${selected ? ' aria-current="true"' : ""}><span class="${icon}" aria-hidden="true"></span>${escapeHtml(item.nom)}</a>`;
    })
    .join("")}</nav>`;
}

// ------------------------------------------------------------ liste : tri, affichage, éléments
const SORT_OPTIONS = [
  ["nom", 1, t("Nom (A → Z)")],
  ["nom", -1, t("Nom (Z → A)")],
  ["modifie", -1, t("Date (récent d'abord)")],
  ["modifie", 1, t("Date (ancien d'abord)")],
  ["taille", -1, t("Taille (gros d'abord)")],
  ["taille", 1, t("Taille (petit d'abord)")],
];
const DEFAULT_VIEW = { vue: "liste", tri: "nom", sens: 1 };
const collator = new Intl.Collator("fr", { numeric: true, sensitivity: "base" });

function readViewPreferences(path) {
  try {
    const saved = JSON.parse(localStorage.getItem("jabba-vue:" + path) || "null");
    return { ...DEFAULT_VIEW, ...(saved || {}) };
  } catch (_) {
    return { ...DEFAULT_VIEW };
  }
}
function saveViewPreferences(path, preferences) {
  try {
    localStorage.setItem("jabba-vue:" + path, JSON.stringify(preferences));
  } catch (_) {
    /* navigation privée : le choix vaut pour cette visite seulement */
  }
}

// Les dossiers restent toujours en haut, comme dans le Finder.
function sortEntries(list, key, direction) {
  const value = (entry) =>
    key === "taille" ? entry.taille || 0 : key === "modifie" ? Date.parse(entry.modifie) || 0 : entry.nom;
  return [...list].sort((a, b) => {
    if (a.dossier !== b.dossier) return a.dossier ? -1 : 1;
    const compared =
      key === "nom" ? collator.compare(a.nom, b.nom) : value(a) - value(b) || collator.compare(a.nom, b.nom);
    return compared * direction;
  });
}

const checkboxHtml = (item, index) =>
  t`<input type="checkbox" class="case" data-choix="${index}" aria-label="Sélectionner ${escapeHtml(item.nom)}">`;
const nameLinkHtml = (item, index, link) =>
  item.dossier
    ? `<a href="${link(item.chemin)}">${escapeHtml(item.nom)}</a>`
    : `<a href="${downloadUrl(item.chemin)}" data-ouvrir="${index}">${escapeHtml(item.nom)}</a>`;
const dropTarget = (item) => (item.dossier && item.ecriture ? ` data-dossier="${escapeHtml(item.chemin)}"` : "");

function rowHtml(item, index, link) {
  return t`
        <div class="ligne element" data-i="${index}"${dropTarget(item)}>
          <span class="nom">${checkboxHtml(item, index)}${entryIcon(item)}
            <span>${nameLinkHtml(item, index, link)}
            <span class="mobile-detail">${formatBytes(item.taille)} · ${formatWhen(item.modifie)}</span></span></span>
          <span class="gris col-l">${formatBytes(item.taille)}</span>
          <span class="gris col-l">${formatWhen(item.modifie)}</span>
          <button class="btn-menu" type="button" aria-label="Actions pour ${escapeHtml(item.nom)}">•••</button>
        </div>`;
}

function tileHtml(item, index, link) {
  return t`
        <div class="tuile-fichier element" data-i="${index}"${dropTarget(item)}>
          ${checkboxHtml(item, index)}
          <span class="apercu-tuile">${entryIcon(item, 256)}</span>
          <span class="nom-tuile-fichier">${nameLinkHtml(item, index, link)}</span>
          <span class="note">${item.dossier ? t("Dossier") : formatBytes(item.taille)}</span>
          <button class="btn-menu" type="button" aria-label="Actions pour ${escapeHtml(item.nom)}">•••</button>
        </div>`;
}

pages.fichiers = async (params) => {
  if (params.get("q")) return showSearchResults(params.get("q"));
  const path = params.get("chemin") || "/";
  rememberFolder(path);
  content.innerHTML = t`<div class="entete"><div><h1>Fichiers</h1></div></div>${loadingHtml(5)}`;
  const [data, locations] = await Promise.all([
    api("/api/fichiers?chemin=" + encodeURIComponent(path)),
    readLocations(),
  ]);
  const parts = path.split("/").filter(Boolean);
  const link = (item) => "#/fichiers?chemin=" + encodeURIComponent(item);
  const thread = [
    `<a href="${link("/")}"${data.source === "disque" ? "" : ' data-chemin="/"'}>${data.source === "disque" ? t("Serveur") : t("Nextcloud")}</a>`,
  ]
    .concat(
      parts.map((value, index) =>
        index === parts.length - 1
          ? `<strong>${escapeHtml(value)}</strong>`
          : `<a href="${link("/" + parts.slice(0, index + 1).join("/"))}" data-chemin="${escapeHtml("/" + parts.slice(0, index + 1).join("/"))}">${escapeHtml(value)}</a>`,
      ),
    )
    .join('<span class="sep">/</span>');
  const title = parts.length ? parts[parts.length - 1] : t("Fichiers");
  const preferences = readViewPreferences(path);
  const entries = sortEntries(data.elements, preferences.tri, preferences.sens);
  const grid = preferences.vue === "grille";
  const sortHeader = (key, label, extraClass = "") => {
    const active = preferences.tri === key;
    return t`<span class="${extraClass}"><button class="tri${active ? " actif" : ""}" type="button" data-tri="${key}" aria-label="Trier par ${label.toLowerCase()}${active ? (preferences.sens === 1 ? t(", ordre croissant") : t(", ordre décroissant")) : ""}">${label}${active ? (preferences.sens === 1 ? " ↑" : " ↓") : ""}</button></span>`;
  };
  content.innerHTML = t`
    <div class="entete">
      <div><h1>${escapeHtml(title)}${data.ecriture ? "" : t('<span class="lecture-seule">Lecture seule</span>')}</h1>
        <p class="sous-titre">${data.source === "disque" ? t("Disque du serveur (Nextcloud désactivé)") : t("Ton Nextcloud · synchronisé avec l'iPhone et le Finder")}</p></div>
      <div class="actions">
        ${path !== "/" ? `<button class="btn" id="etoile" aria-pressed="${data.favori}">${data.favori ? t("★ Favori") : t("☆ Favori")}</button>` : ""}
        <a class="btn" href="#/corbeille">Corbeille</a>
        ${data.lien_nextcloud ? t`<a class="btn" href="${escapeHtml(data.lien_nextcloud)}" target="_blank" rel="noopener">Ouvrir dans Nextcloud</a>` : ""}
        ${data.ecriture ? t`<button class="btn" id="nouveau">Nouveau dossier</button><button class="btn" id="envoyer-dossier">Envoyer un dossier</button><button class="btn btn-p" id="envoyer">Envoyer des fichiers</button>` : ""}
      </div>
    </div>
    ${searchBarHtml()}
    ${locationPickerHtml(locations, path, link)}
    <div class="ligne-nav">${navigationBarHtml(path, link, data.source === "disque" ? t("Serveur") : t("Nextcloud"))}
    <nav class="fil" aria-label="Emplacement">${thread}</nav></div>
    <div class="outils-liste">
      <label class="choix-tri"><span>Trier par</span>
        <select id="tri" aria-label="Trier par">${SORT_OPTIONS.map(([key, sens, label]) => `<option value="${key}:${sens}"${preferences.tri === key && preferences.sens === sens ? " selected" : ""}>${label}</option>`).join("")}</select></label>
      <div class="bascule-vue" role="group" aria-label="Affichage">
        <button class="btn btn-petit${grid ? "" : " actif"}" type="button" data-vue="liste" aria-pressed="${!grid}">Liste</button>
        <button class="btn btn-petit${grid ? " actif" : ""}" type="button" data-vue="grille" aria-pressed="${grid}">Grille</button>
      </div>
      ${entries.length ? t`<button class="btn btn-petit" type="button" id="tout-selectionner">Tout sélectionner</button>` : ""}
      <button class="lien" type="button" id="aide-clavier" aria-expanded="false">Raccourcis clavier</button>
    </div>
    <dl class="raccourcis" id="raccourcis" hidden>${SHORTCUTS.map(([keys, label]) => `<dt><kbd>${escapeHtml(keys)}</kbd></dt><dd>${escapeHtml(label)}</dd>`).join("")}</dl>
    <section class="carte tableau${grid ? " vue-grille" : ""}" aria-label="Contenu du dossier">
      ${grid ? "" : `<div class="ligne titres">${sortHeader("nom", t("Nom"))}${sortHeader("taille", t("Taille"), "col-l")}${sortHeader("modifie", t("Modifié"), "col-l")}<span></span></div>`}
      ${
        entries.length
          ? grid
            ? `<div class="grille-fichiers">${entries.map((item, index) => tileHtml(item, index, link)).join("")}</div>`
            : entries.map((item, index) => rowHtml(item, index, link)).join("")
          : t`<div class="vide">Ce dossier est vide.</div>`
      }
      ${data.ecriture ? t`<div class="depot" id="depot">Glisse des fichiers ou des dossiers ici pour les envoyer dans « ${escapeHtml(title)} »</div>` : ""}
    </section>
    <input type="file" id="choix" multiple hidden>
    <input type="file" id="choix-dossier" webkitdirectory multiple hidden>`;

  const reload = () => pages.fichiers(params).catch(showError);
  $("#nav-prec").onclick = () => goInHistory(-1);
  $("#nav-suiv").onclick = () => goInHistory(1);
  const parentLink = content.querySelector(".nav-fichiers a");
  if (parentLink) parentLink.id = "nav-fichiers-parent";
  wireSearch();
  const openAt = (index) => {
    const entry = entries[index];
    if (entry.dossier) location.hash = link(entry.chemin);
    else if (kindOf(entry.nom)) openViewer(entries, index);
    else location.href = downloadUrl(entry.chemin);
  };
  content.querySelectorAll("[data-ouvrir]").forEach((element) =>
    element.addEventListener("click", (event) => {
      const entry = entries[+element.dataset.ouvrir];
      if (kindOf(entry.nom)) {
        event.preventDefault();
        openViewer(entries, +element.dataset.ouvrir);
      }
    }),
  );
  // Tri et affichage, mémorisés pour ce dossier.
  const savePreference = (change) => {
    saveViewPreferences(path, { ...preferences, ...change });
    reload();
  };
  content.querySelectorAll("[data-tri]").forEach((button) => {
    button.onclick = () =>
      savePreference({
        tri: button.dataset.tri,
        sens: preferences.tri === button.dataset.tri ? -preferences.sens : button.dataset.tri === "nom" ? 1 : -1,
      });
  });
  $("#tri").onchange = (event) => {
    const [key, sens] = event.target.value.split(":");
    savePreference({ tri: key, sens: +sens });
  };
  content.querySelectorAll("[data-vue]").forEach((button) => {
    button.onclick = () => savePreference({ vue: button.dataset.vue });
  });
  $("#aide-clavier").onclick = (event) => {
    const list = $("#raccourcis");
    list.hidden = !list.hidden;
    event.target.setAttribute("aria-expanded", String(!list.hidden));
  };
  const star = $("#etoile");
  if (star)
    star.onclick = async () => {
      try {
        await toggleFavorite(path, data.favori);
      } catch (error) {
        toast(error.message, true);
      }
      reload();
    };
  const send = (items) => uploadItems(path, items, reload);

  if (data.ecriture) {
    $("#nouveau").onclick = async () => {
      const name = await confirmAction({ titre: t("Nouveau dossier"), champ: "", valider: t("Créer") });
      if (!name) return;
      if (/[\\/]/.test(name)) return toast(t("Le nom ne peut pas contenir / ou \\"), true);
      try {
        await api("/api/fichiers/dossier", { method: "POST", json: { chemin: path + "/" + name } });
        toast(t("Dossier créé"));
      } catch (error) {
        toast(error.message, true);
      }
      reload();
    };
    const choices = $("#choix");
    const folderChoice = $("#choix-dossier");
    $("#envoyer").onclick = () => choices.click();
    $("#envoyer-dossier").onclick = () => folderChoice.click();
    [choices, folderChoice].forEach((input) => {
      input.onchange = () => {
        send(pickedItems(input.files));
        input.value = "";
      };
    });
    const dropZone = $("#depot"),
      area = $(".tableau");
    const fromFinder = (event) => event.dataTransfer.types.includes("Files");
    ["dragenter", "dragover"].forEach((value) =>
      area.addEventListener(value, (event) => {
        if (!fromFinder(event)) return; // un glisser depuis la liste vise un dossier, pas cette zone
        event.preventDefault();
        dropZone.classList.add("survol");
      }),
    );
    ["dragleave", "drop"].forEach((value) =>
      area.addEventListener(value, (event) => {
        if (value === "drop" || !area.contains(event.relatedTarget)) dropZone.classList.remove("survol");
      }),
    );
    area.addEventListener("drop", async (event) => {
      if (event.jabbaPris || !fromFinder(event)) return; // déjà pris par un dossier de la liste
      event.preventDefault();
      send(await droppedItems(event.dataTransfer));
    });
  }

  // Glisser sur un dossier de la liste, ou sur le fil d'Ariane : envoi depuis le Finder, ou déplacement.
  content.querySelectorAll(".element[data-dossier]").forEach((row) => enableDrop(row, row.dataset.dossier, reload));
  if (data.ecriture)
    content.querySelectorAll(".fil a[data-chemin]").forEach((crumb) => enableDrop(crumb, crumb.dataset.chemin, reload));

  const selectAllButton = $("#tout-selectionner");
  if (selectAllButton) selectAllButton.onclick = selectAll;
  attachSelection(entries, {
    folder: data,
    publicOpen: data.source === "nextcloud" && (await publicModeOpen()),
    after: reload,
    open: openAt,
    link,
    addToQuickAccess: async (folderPath) => {
      try {
        await toggleFavorite(folderPath, false);
      } catch (error) {
        toast(error.message, true);
      }
    },
  });
};
