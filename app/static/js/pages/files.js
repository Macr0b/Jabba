// Écran Fichiers : parcourir, chercher, envoyer, déplacer, supprimer (la Corbeille : pages/trash.js).
//
// Sommaire :
//   partagé : pages.fichiers (écran)
//   interne : openEntry, readViewPreferences, fromOldNames, saveViewPreferences, sortEntries, rowHtml, tileHtml, searchBarHtml, wireSearch, showSearchResults, filesHistory, rememberFolder, goInHistory, navigationBarHtml, locationsCache, readLocations, locationPickerHtml, checkboxHtml, nameLinkHtml, dropTarget
import { confirmAction, dialogElement, toast } from "../components/dialogs.js";
import { attachSelection, toggleAll, SHORTCUTS } from "../components/file_selection.js";
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
  if (entry.folder) location.hash = folderLink(entry.path);
  else if (kindOf(entry.name)) openViewer(list, index);
  else location.href = downloadUrl(entry.path);
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
  const element = data.items;
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
            <span><a href="#" data-ouvrir="${index}">${escapeHtml(item.name)}</a>
            <span class="detail-chemin"><a href="${folderLink(item.path.split("/").slice(0, -1).join("/") || "/")}">${escapeHtml(item.path.split("/").slice(0, -1).join("/") || "/")}</a></span></span></span>
          <span class="gris col-l">${item.folder ? t("Dossier") : formatBytes(item.size)}</span>
          <span class="gris col-l">${formatWhen(item.modified)}</span>
          <span></span>
        </div>`,
              )
              .join("")
          : t`<div class="vide">Rien trouvé. Essaie un autre mot, ou une partie du nom.</div>`
      }
    </section>
    ${data.full ? "" : t`<p class="note">La recherche s'est arrêtée avant la fin (trop de fichiers). Précise ta recherche.</p>`}`;
  wireSearch();
  content.querySelectorAll("[data-ouvrir]").forEach((node) =>
    node.addEventListener("click", (event) => {
      event.preventDefault();
      openEntry(element, +node.dataset.ouvrir);
    }),
  );
}

// Historique de navigation dans Fichiers : Précédent / Suivant, comme dans le Finder.
const filesHistory = { stack: [], pos: -1, skip: null };
function rememberFolder(path) {
  const history = filesHistory;
  if (history.skip !== null && history.stack[history.skip] === path) history.pos = history.skip;
  else if (history.stack[history.pos - 1] === path)
    history.pos -= 1; // retour du navigateur ou de la souris
  else if (history.stack[history.pos + 1] === path) history.pos += 1;
  else if (history.stack[history.pos] !== path) {
    history.stack = history.stack
      .slice(0, history.pos + 1)
      .concat(path)
      .slice(-100);
    history.pos = history.stack.length - 1;
  }
  history.skip = null;
}
function goInHistory(direction) {
  const history = filesHistory;
  const target = history.pos + direction;
  if (target < 0 || target >= history.stack.length) return;
  history.skip = target;
  location.hash = "#/fichiers?chemin=" + encodeURIComponent(history.stack[target]);
}
function navigationBarHtml(path, link, root) {
  const history = filesHistory;
  const parent = "/" + path.split("/").filter(Boolean).slice(0, -1).join("/");
  return t`<div class="nav-fichiers" role="group" aria-label="Navigation">
    <button class="btn btn-petit" id="nav-prec" title="Précédent (⌘[)" aria-label="Précédent"${history.pos > 0 ? "" : " disabled"}>‹</button>
    <button class="btn btn-petit" id="nav-suiv" title="Suivant (⌘])" aria-label="Suivant"${history.pos < history.stack.length - 1 ? "" : " disabled"}>›</button>
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
    locationsCache.v = data.essentials;
    locationsCache.t = Date.now();
  } catch (_) {
    locationsCache.v = locationsCache.v || [];
  }
  return locationsCache.v;
}
function locationPickerHtml(list, path, link) {
  if (!list || list.length < 2) return "";
  const active = [...list].filter(
    (item) => item.path !== "/" && (path === item.path || path.startsWith(item.path + "/")),
  )[0];
  return t`<nav class="emplacements" aria-label="Emplacement">${list
    .map((item) => {
      const selected = active ? active === item : item.path === "/";
      const icon = item.type === "disque" ? "ic-disque" : item.type === "racine" ? "ic-maison" : "ic-dossier";
      return `<a class="puce-emplacement${selected ? " actif" : ""}" href="${link(item.path)}"${selected ? ' aria-current="true"' : ""}><span class="${icon}" aria-hidden="true"></span>${escapeHtml(item.name)}</a>`;
    })
    .join("")}</nav>`;
}

// ------------------------------------------------------------ liste : tri, affichage, éléments
const SORT_OPTIONS = [
  ["name", 1, t("Nom (A → Z)")],
  ["name", -1, t("Nom (Z → A)")],
  ["modified", -1, t("Date (récent d'abord)")],
  ["modified", 1, t("Date (ancien d'abord)")],
  ["size", -1, t("Taille (gros d'abord)")],
  ["size", 1, t("Taille (petit d'abord)")],
];
const DEFAULT_VIEW = { view: "liste", sort: "name", direction: 1 };
const collator = new Intl.Collator("fr", { numeric: true, sensitivity: "base" });

function readViewPreferences(path) {
  try {
    const saved = JSON.parse(localStorage.getItem("jabba-vue:" + path) || "null") || {};
    return { ...DEFAULT_VIEW, ...fromOldNames(saved) };
  } catch (_) {
    return { ...DEFAULT_VIEW };
  }
}
// Choix gardés avant la 0.17 : noms français (vue, tri, sens), à relire une fois.
const OLD_SORT_KEYS = { nom: "name", taille: "size", modifie: "modified" };
function fromOldNames(saved) {
  if (!("vue" in saved || "tri" in saved || "sens" in saved)) return saved;
  return { view: saved.vue || "liste", sort: OLD_SORT_KEYS[saved.tri] || "name", direction: saved.sens || 1 };
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
    key === "size" ? entry.size || 0 : key === "modified" ? Date.parse(entry.modified) || 0 : entry.name;
  return [...list].sort((a, b) => {
    if (a.folder !== b.folder) return a.folder ? -1 : 1;
    const compared =
      key === "name" ? collator.compare(a.name, b.name) : value(a) - value(b) || collator.compare(a.name, b.name);
    return compared * direction;
  });
}

const checkboxHtml = (item, index) =>
  t`<input type="checkbox" class="case" data-choix="${index}" aria-label="Sélectionner ${escapeHtml(item.name)}">`;
const nameLinkHtml = (item, index, link) =>
  item.folder
    ? `<a href="${link(item.path)}">${escapeHtml(item.name)}</a>`
    : `<a href="${downloadUrl(item.path)}" data-ouvrir="${index}">${escapeHtml(item.name)}</a>`;
const dropTarget = (item) => (item.folder && item.writable ? ` data-dossier="${escapeHtml(item.path)}"` : "");

function rowHtml(item, index, link) {
  return t`
        <div class="ligne element" data-i="${index}"${dropTarget(item)}>
          <span class="nom">${checkboxHtml(item, index)}${entryIcon(item)}
            <span>${nameLinkHtml(item, index, link)}
            <span class="mobile-detail">${formatBytes(item.size)} · ${formatWhen(item.modified)}</span></span></span>
          <span class="gris col-l">${formatBytes(item.size)}</span>
          <span class="gris col-l">${formatWhen(item.modified)}</span>
          <button class="btn-menu" type="button" aria-label="Actions pour ${escapeHtml(item.name)}">•••</button>
        </div>`;
}

function tileHtml(item, index, link) {
  return t`
        <div class="tuile-fichier element" data-i="${index}"${dropTarget(item)}>
          ${checkboxHtml(item, index)}
          <span class="apercu-tuile">${entryIcon(item, 256)}</span>
          <span class="nom-tuile-fichier">${nameLinkHtml(item, index, link)}</span>
          <span class="note">${item.folder ? t("Dossier") : formatBytes(item.size)}</span>
          <button class="btn-menu" type="button" aria-label="Actions pour ${escapeHtml(item.name)}">•••</button>
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
  const entries = sortEntries(data.items, preferences.sort, preferences.direction);
  const grid = preferences.view === "grille";
  const sortHeader = (key, label, extraClass = "") => {
    const active = preferences.sort === key;
    return t`<span class="${extraClass}"><button class="tri${active ? " actif" : ""}" type="button" data-tri="${key}" aria-label="Trier par ${label.toLowerCase()}${active ? (preferences.direction === 1 ? t(", ordre croissant") : t(", ordre décroissant")) : ""}">${label}${active ? (preferences.direction === 1 ? " ↑" : " ↓") : ""}</button></span>`;
  };
  content.innerHTML = t`
    <div class="entete">
      <div><h1>${escapeHtml(title)}${data.writable ? "" : t('<span class="lecture-seule">Lecture seule</span>')}</h1>
        <p class="sous-titre">${data.source === "disque" ? t("Disque du serveur (Nextcloud désactivé)") : t("Ton Nextcloud · synchronisé avec l'iPhone et le Finder")}</p></div>
      <div class="actions">
        ${path !== "/" ? `<button class="btn" id="etoile" aria-pressed="${data.favorite}">${data.favorite ? t("★ Favori") : t("☆ Favori")}</button>` : ""}
        <a class="btn" href="#/corbeille">Corbeille</a>
        ${data.nextcloud_link ? t`<a class="btn" href="${escapeHtml(data.nextcloud_link)}" target="_blank" rel="noopener">Ouvrir dans Nextcloud</a>` : ""}
        ${data.writable ? t`<button class="btn" id="nouveau">Nouveau dossier</button><button class="btn" id="envoyer-dossier">Envoyer un dossier</button><button class="btn btn-p" id="envoyer">Envoyer des fichiers</button>` : ""}
      </div>
    </div>
    ${searchBarHtml()}
    ${locationPickerHtml(locations, path, link)}
    <div class="ligne-nav">${navigationBarHtml(path, link, data.source === "disque" ? t("Serveur") : t("Nextcloud"))}
    <nav class="fil" aria-label="Emplacement">${thread}</nav></div>
    <div class="outils-liste">
      <label class="choix-tri"><span>Trier par</span>
        <select id="tri" aria-label="Trier par">${SORT_OPTIONS.map(([key, sens, label]) => `<option value="${key}:${sens}"${preferences.sort === key && preferences.direction === sens ? " selected" : ""}>${label}</option>`).join("")}</select></label>
      <div class="bascule-vue" role="group" aria-label="Affichage">
        <button class="btn btn-petit${grid ? "" : " actif"}" type="button" data-vue="liste" aria-pressed="${!grid}">Liste</button>
        <button class="btn btn-petit${grid ? " actif" : ""}" type="button" data-vue="grille" aria-pressed="${grid}">Grille</button>
      </div>
      ${entries.length ? t`<button class="btn btn-petit" type="button" id="tout-selectionner">Tout sélectionner</button>` : ""}
      <button class="lien" type="button" id="aide-clavier" aria-expanded="false">Raccourcis clavier</button>
    </div>
    <dl class="raccourcis" id="raccourcis" hidden>${SHORTCUTS.map(([keys, label]) => `<dt><kbd>${escapeHtml(keys)}</kbd></dt><dd>${escapeHtml(label)}</dd>`).join("")}</dl>
    <section class="carte tableau${grid ? " vue-grille" : ""}" aria-label="Contenu du dossier">
      ${grid ? "" : `<div class="ligne titres">${sortHeader("name", t("Nom"))}${sortHeader("size", t("Taille"), "col-l")}${sortHeader("modified", t("Modifié"), "col-l")}<span></span></div>`}
      ${
        entries.length
          ? grid
            ? `<div class="grille-fichiers">${entries.map((item, index) => tileHtml(item, index, link)).join("")}</div>`
            : entries.map((item, index) => rowHtml(item, index, link)).join("")
          : t`<div class="vide">Ce dossier est vide.</div>`
      }
      ${data.writable ? t`<div class="depot" id="depot">Glisse des fichiers ou des dossiers ici pour les envoyer dans « ${escapeHtml(title)} »</div>` : ""}
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
    if (entry.folder) location.hash = link(entry.path);
    else if (kindOf(entry.name)) openViewer(entries, index);
    else location.href = downloadUrl(entry.path);
  };
  content.querySelectorAll("[data-ouvrir]").forEach((element) =>
    element.addEventListener("click", (event) => {
      const entry = entries[+element.dataset.ouvrir];
      if (kindOf(entry.name)) {
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
        sort: button.dataset.tri,
        direction:
          preferences.sort === button.dataset.tri ? -preferences.direction : button.dataset.tri === "name" ? 1 : -1,
      });
  });
  $("#tri").onchange = (event) => {
    const [key, sens] = event.target.value.split(":");
    savePreference({ sort: key, direction: +sens });
  };
  content.querySelectorAll("[data-vue]").forEach((button) => {
    button.onclick = () => savePreference({ view: button.dataset.vue });
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
        await toggleFavorite(path, data.favorite);
      } catch (error) {
        toast(error.message, true);
      }
      reload();
    };
  const send = (items) => uploadItems(path, items, reload);

  if (data.writable) {
    $("#nouveau").onclick = async () => {
      const name = await confirmAction({ title: t("Nouveau dossier"), field: "", confirmLabel: t("Créer") });
      if (!name) return;
      if (/[\\/]/.test(name)) return toast(t("Le nom ne peut pas contenir / ou \\"), true);
      try {
        await api("/api/fichiers/dossier", { method: "POST", json: { path: path + "/" + name } });
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
    const dropZone = $("#depot");
    const area = $(".tableau");
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
  if (data.writable)
    content.querySelectorAll(".fil a[data-chemin]").forEach((crumb) => enableDrop(crumb, crumb.dataset.chemin, reload));

  const selectAllButton = $("#tout-selectionner");
  if (selectAllButton) selectAllButton.onclick = toggleAll;
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
