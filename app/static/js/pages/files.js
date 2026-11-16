// Écran Fichiers : parcourir, chercher, envoyer, déplacer, supprimer ; et la Corbeille.
//
// Sommaire :
//   partagé : pages.fichiers (écran), pages.corbeille (écran)
//   interne : openEntry, readViewPreferences, saveViewPreferences, sortEntries, rowHtml, tileHtml, searchBarHtml, wireSearch, showSearchResults, filesHistory, rememberFolder, goInHistory, navigationBarHtml, locationsCache, readLocations, locationPickerHtml
import { confirmAction, dialogElement, chooseFromMenu, toast } from "../components/dialogs.js";
import { attachSelection, SHORTCUTS } from "../components/file_selection.js";
import { publicModeOpen } from "../components/public_mode_card.js";
import { uploadFiles } from "../components/upload_queue.js";
import { kindOf, entryIcon, downloadUrl, openViewer } from "../components/viewer.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatBytes, formatWhen } from "../core/format.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { loadingHtml } from "../core/tasks.js";
import { enableDrop, toggleFavorite, folderLink } from "./home.js";

function openEntry(list, index) {
  const entry = list[index];
  if (entry.dossier) location.hash = folderLink(entry.chemin);
  else if (kindOf(entry.nom)) openViewer(list, index);
  else location.href = downloadUrl(entry.chemin);
}

const searchBarHtml = (query = "") => `<form class="recherche" id="form-recherche" role="search">
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
  content.innerHTML = `<div class="entete"><div><h1>Recherche</h1><p class="sous-titre">« ${escapeHtml(query)} »</p></div></div>${searchBarHtml(query)}${loadingHtml(4)}`;
  wireSearch();
  const data = await api("/api/recherche?q=" + encodeURIComponent(query));
  const element = data.elements;
  content.innerHTML = `
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
          <span class="gris col-l">${item.dossier ? "Dossier" : formatBytes(item.taille)}</span>
          <span class="gris col-l">${formatWhen(item.modifie)}</span>
          <span></span>
        </div>`,
              )
              .join("")
          : `<div class="vide">Rien trouvé. Essaie un autre mot, ou une partie du nom.</div>`
      }
    </section>
    ${data.complet ? "" : `<p class="note">La recherche s'est arrêtée avant la fin (trop de fichiers). Précise ta recherche.</p>`}`;
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
  return `<div class="nav-fichiers" role="group" aria-label="Navigation">
    <button class="btn btn-petit" id="nav-prec" title="Précédent (⌘[)" aria-label="Précédent"${history.pos > 0 ? "" : " disabled"}>‹</button>
    <button class="btn btn-petit" id="nav-suiv" title="Suivant (⌘])" aria-label="Suivant"${history.pos < history.pile.length - 1 ? "" : " disabled"}>›</button>
    ${
      path !== "/"
        ? `<a class="btn btn-petit" href="${link(parent)}" title="Dossier parent (⌘↑)">↑ Dossier parent</a>
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
  return `<nav class="emplacements" aria-label="Emplacement">${list
    .map((item) => {
      const selected = active ? active === item : item.chemin === "/";
      const icon = item.type === "disque" ? "ic-disque" : item.type === "racine" ? "ic-maison" : "ic-dossier";
      return `<a class="puce-emplacement${selected ? " actif" : ""}" href="${link(item.chemin)}"${selected ? ' aria-current="true"' : ""}><span class="${icon}" aria-hidden="true"></span>${escapeHtml(item.nom)}</a>`;
    })
    .join("")}</nav>`;
}

// ------------------------------------------------------------ liste : tri, affichage, éléments
const SORT_OPTIONS = [
  ["nom", 1, "Nom (A → Z)"],
  ["nom", -1, "Nom (Z → A)"],
  ["modifie", -1, "Date (récent d'abord)"],
  ["modifie", 1, "Date (ancien d'abord)"],
  ["taille", -1, "Taille (gros d'abord)"],
  ["taille", 1, "Taille (petit d'abord)"],
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
  `<input type="checkbox" class="case" data-choix="${index}" aria-label="Sélectionner ${escapeHtml(item.nom)}">`;
const nameLinkHtml = (item, index, link) =>
  item.dossier
    ? `<a href="${link(item.chemin)}">${escapeHtml(item.nom)}</a>`
    : `<a href="${downloadUrl(item.chemin)}" data-ouvrir="${index}">${escapeHtml(item.nom)}</a>`;
const dropTarget = (item) => (item.dossier && item.ecriture ? ` data-dossier="${escapeHtml(item.chemin)}"` : "");

function rowHtml(item, index, link) {
  return `
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
  return `
        <div class="tuile-fichier element" data-i="${index}"${dropTarget(item)}>
          ${checkboxHtml(item, index)}
          <span class="apercu-tuile">${entryIcon(item, 256)}</span>
          <span class="nom-tuile-fichier">${nameLinkHtml(item, index, link)}</span>
          <span class="note">${item.dossier ? "Dossier" : formatBytes(item.taille)}</span>
          <button class="btn-menu" type="button" aria-label="Actions pour ${escapeHtml(item.nom)}">•••</button>
        </div>`;
}

pages.fichiers = async (params) => {
  if (params.get("q")) return showSearchResults(params.get("q"));
  const path = params.get("chemin") || "/";
  rememberFolder(path);
  content.innerHTML = `<div class="entete"><div><h1>Fichiers</h1></div></div>${loadingHtml(5)}`;
  const [data, locations] = await Promise.all([
    api("/api/fichiers?chemin=" + encodeURIComponent(path)),
    readLocations(),
  ]);
  const parts = path.split("/").filter(Boolean);
  const link = (item) => "#/fichiers?chemin=" + encodeURIComponent(item);
  const thread = [`<a href="${link("/")}">${data.source === "disque" ? "Serveur" : "Nextcloud"}</a>`]
    .concat(
      parts.map((value, index) =>
        index === parts.length - 1
          ? `<strong>${escapeHtml(value)}</strong>`
          : `<a href="${link("/" + parts.slice(0, index + 1).join("/"))}">${escapeHtml(value)}</a>`,
      ),
    )
    .join('<span class="sep">/</span>');
  const title = parts.length ? parts[parts.length - 1] : "Fichiers";
  const preferences = readViewPreferences(path);
  const entries = sortEntries(data.elements, preferences.tri, preferences.sens);
  const grid = preferences.vue === "grille";
  const sortHeader = (key, label, extraClass = "") => {
    const active = preferences.tri === key;
    return `<span class="${extraClass}"><button class="tri${active ? " actif" : ""}" type="button" data-tri="${key}" aria-label="Trier par ${label.toLowerCase()}${active ? (preferences.sens === 1 ? ", ordre croissant" : ", ordre décroissant") : ""}">${label}${active ? (preferences.sens === 1 ? " ↑" : " ↓") : ""}</button></span>`;
  };
  content.innerHTML = `
    <div class="entete">
      <div><h1>${escapeHtml(title)}${data.ecriture ? "" : '<span class="lecture-seule">Lecture seule</span>'}</h1>
        <p class="sous-titre">${data.source === "disque" ? "Disque du serveur (Nextcloud désactivé)" : "Ton Nextcloud · synchronisé avec l'iPhone et le Finder"}</p></div>
      <div class="actions">
        ${path !== "/" ? `<button class="btn" id="etoile" aria-pressed="${data.favori}">${data.favori ? "★ Favori" : "☆ Favori"}</button>` : ""}
        <a class="btn" href="#/corbeille">Corbeille</a>
        ${data.lien_nextcloud ? `<a class="btn" href="${escapeHtml(data.lien_nextcloud)}" target="_blank" rel="noopener">Ouvrir dans Nextcloud</a>` : ""}
        ${data.ecriture ? `<button class="btn" id="nouveau">Nouveau dossier</button><button class="btn btn-p" id="envoyer">Envoyer des fichiers</button>` : ""}
      </div>
    </div>
    ${searchBarHtml()}
    ${locationPickerHtml(locations, path, link)}
    <div class="ligne-nav">${navigationBarHtml(path, link, data.source === "disque" ? "Serveur" : "Nextcloud")}
    <nav class="fil" aria-label="Emplacement">${thread}</nav></div>
    <div class="outils-liste">
      <label class="choix-tri"><span>Trier par</span>
        <select id="tri" aria-label="Trier par">${SORT_OPTIONS.map(([key, sens, label]) => `<option value="${key}:${sens}"${preferences.tri === key && preferences.sens === sens ? " selected" : ""}>${label}</option>`).join("")}</select></label>
      <div class="bascule-vue" role="group" aria-label="Affichage">
        <button class="btn btn-petit${grid ? "" : " actif"}" type="button" data-vue="liste" aria-pressed="${!grid}">Liste</button>
        <button class="btn btn-petit${grid ? " actif" : ""}" type="button" data-vue="grille" aria-pressed="${grid}">Grille</button>
      </div>
      <button class="lien" type="button" id="aide-clavier" aria-expanded="false">Raccourcis clavier</button>
    </div>
    <dl class="raccourcis" id="raccourcis" hidden>${SHORTCUTS.map(([keys, label]) => `<dt><kbd>${escapeHtml(keys)}</kbd></dt><dd>${escapeHtml(label)}</dd>`).join("")}</dl>
    <section class="carte tableau${grid ? " vue-grille" : ""}" aria-label="Contenu du dossier">
      ${grid ? "" : `<div class="ligne titres">${sortHeader("nom", "Nom")}${sortHeader("taille", "Taille", "col-l")}${sortHeader("modifie", "Modifié", "col-l")}<span></span></div>`}
      ${
        entries.length
          ? grid
            ? `<div class="grille-fichiers">${entries.map((item, index) => tileHtml(item, index, link)).join("")}</div>`
            : entries.map((item, index) => rowHtml(item, index, link)).join("")
          : `<div class="vide">Ce dossier est vide.</div>`
      }
      ${data.ecriture ? `<div class="depot" id="depot">Glisse des fichiers ici pour les envoyer dans « ${escapeHtml(title)} »</div>` : ""}
    </section>
    <input type="file" id="choix" multiple hidden>`;

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
  const send = (files) => uploadFiles(path, files, reload);

  if (data.ecriture) {
    $("#nouveau").onclick = async () => {
      const name = await confirmAction({ titre: "Nouveau dossier", champ: "", valider: "Créer" });
      if (!name) return;
      if (/[\\/]/.test(name)) return toast("Le nom ne peut pas contenir / ou \\", true);
      try {
        await api("/api/fichiers/dossier", { method: "POST", json: { chemin: path + "/" + name } });
        toast("Dossier créé");
      } catch (error) {
        toast(error.message, true);
      }
      reload();
    };
    const choices = $("#choix");
    $("#envoyer").onclick = () => choices.click();
    choices.onchange = () => {
      send([...choices.files]);
      choices.value = "";
    };
    const dropZone = $("#depot"),
      area = $(".tableau");
    ["dragenter", "dragover"].forEach((value) =>
      area.addEventListener(value, (event) => {
        event.preventDefault();
        dropZone.classList.add("survol");
      }),
    );
    ["dragleave", "drop"].forEach((value) =>
      area.addEventListener(value, (event) => {
        event.preventDefault();
        if (value === "drop" || !area.contains(event.relatedTarget)) dropZone.classList.remove("survol");
      }),
    );
    area.addEventListener("drop", (event) => {
      if (!event.defaultPrevented || !event.jabbaPris) send([...event.dataTransfer.files]);
    });
  }

  // Glisser des fichiers directement sur un dossier de la liste.
  content.querySelectorAll(".element[data-dossier]").forEach((row) => enableDrop(row, row.dataset.dossier, reload));

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

// ------------------------------------------------------------ corbeille
pages.corbeille = async () => {
  content.innerHTML = `<div class="entete"><div><h1>Corbeille</h1></div></div>${loadingHtml(4)}`;
  const data = await api("/api/corbeille");
  const element = data.elements;
  const days = (value) => Math.max(0, data.garde - Math.floor((Date.now() / 1000 - value) / 86400));
  content.innerHTML = `
    <div class="entete"><div><h1>Corbeille</h1><p class="sous-titre">Ce que tu as supprimé, gardé ${data.garde} jours puis effacé tout seul.</p></div>
      <div class="actions"><a class="btn" href="#/fichiers">← Fichiers</a>${element.length ? `<button class="btn btn-danger" id="vider">Vider la corbeille</button>` : ""}</div></div>
    <section class="carte tableau" aria-label="Éléments supprimés">
      <div class="ligne titres"><span>Nom</span><span class="col-l">Taille</span><span class="col-l">Supprimé</span><span></span></div>
      ${
        element.length
          ? element
              .map(
                (item, index) => `
        <div class="ligne">
          <span class="nom">${item.dossier ? '<span class="ic-dossier" aria-hidden="true"></span>' : `<span class="ic-fichier${kindOf(item.nom) ? " " + kindOf(item.nom) : ""}" aria-hidden="true"></span>`}
            <span>${escapeHtml(item.nom)}<span class="detail-chemin">${item.origine ? "Venait de " + escapeHtml(item.origine) : "Origine inconnue"} · encore ${days(item.supprime)} j</span></span></span>
          <span class="gris col-l">${formatBytes(item.taille)}</span>
          <span class="gris col-l">${formatWhen(new Date(item.supprime * 1000).toISOString())}</span>
          <button class="btn-menu" data-i="${index}" aria-label="Actions pour ${escapeHtml(item.nom)}">•••</button>
        </div>`,
              )
              .join("")
          : `<div class="vide">La corbeille est vide.</div>`
      }
    </section>
    <p class="note">Restaurer remet l'élément à sa place. Effacer pour de bon demande le mot de passe du serveur.</p>`;
  const reload = () => pages.corbeille().catch(showError);
  const emptyButton = $("#vider");
  if (emptyButton)
    emptyButton.onclick = async () => {
      if (
        !(await confirmAction({
          titre: "Vider la corbeille ?",
          texte: `Les ${element.length} éléments seront effacés pour de bon. Impossible de revenir en arrière.`,
          valider: "Tout effacer",
          danger: true,
        }))
      )
        return;
      try {
        await api("/api/corbeille/effacer", { method: "POST", json: { tout: true } });
        toast("Corbeille vidée");
      } catch (error) {
        toast(error.message, true);
      }
      reload();
    };
  content.querySelectorAll(".btn-menu").forEach((node) =>
    node.addEventListener("click", async () => {
      const entry = element[+node.dataset.i];
      const choices = [];
      if (entry.origine)
        choices.push({
          texte: "Restaurer",
          f: async () => {
            try {
              const data = await api("/api/corbeille/restaurer", {
                method: "POST",
                json: { id: entry.id, source: entry.source, origine: entry.origine },
              });
              toast(`Restauré dans ${data.chemin}`);
            } catch (error) {
              toast(error.message, true);
            }
            reload();
          },
        });
      choices.push({
        texte: "Effacer pour de bon",
        danger: true,
        f: async () => {
          if (
            !(await confirmAction({
              titre: `Effacer « ${entry.nom} » pour de bon ?`,
              texte: "Impossible de revenir en arrière.",
              valider: "Effacer",
              danger: true,
            }))
          )
            return;
          try {
            await api("/api/corbeille/effacer", {
              method: "POST",
              json: { id: entry.id, source: entry.source, nom: entry.nom },
            });
            toast("Effacé");
          } catch (error) {
            toast(error.message, true);
          }
          reload();
        },
      });
      const secondChoice = await chooseFromMenu(entry.nom, choices);
      if (secondChoice) setTimeout(secondChoice.f, 50);
    }),
  );
};
