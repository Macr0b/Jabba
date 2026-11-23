// Établi : la mémoire du serveur, et « Ajouter un outil » : ceux qu'on installe d'un bouton (Vaultwarden,
// Immich, Time Machine, Mémoire d'atelier…), rangés par usage (fichiers fixés côté serveur, host/catalog.py).
// Une fois installé, l'outil rejoint « Mes outils » (tool_card.js) : son curseur le met en pause ou le
// relance, sans jamais toucher à ses données ; « Désinstaller » est dans son menu « ••• ».
//
// Sommaire :
//   partagé : memoryBarHtml, memoryBannerHtml, bindCatalog
//   interne : GROUPS, offerCardHtml, offersHtml, enable
import { toast } from "./dialogs.js";
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { formatBytes } from "../core/format.js";
import { t } from "../core/i18n.js";
import { followTask } from "../core/tasks.js";
import { toolIcon } from "../pages/home.js";
import { toolCardHtml } from "./tool_card.js";
import { toolMenuHtml } from "./tool_menu.js";

const LOW_MEMORY = 0.5 * 1024 ** 3; // en dessous, le serveur risque de ramer

/** La barre de mémoire : la part des outils, le reste du système, puis ce qui est libre. */
export function memoryBarHtml(memory) {
  const percent = (bytes) => Math.max(0, Math.min(100, (bytes / (memory.total || 1)) * 100)).toFixed(1);
  const system = Math.max(memory.utilise - memory.outils, 0);
  return `<div class="barre-memoire" role="img" aria-label="${escapeHtml(t`${formatBytes(memory.utilise)} utilisés sur ${formatBytes(memory.total)}`)}">
      <span class="part-outils" style="width:${percent(memory.outils)}%"></span><span class="part-systeme" style="width:${percent(system)}%"></span>
    </div>
    <div class="legende-memoire"><span><i class="part-outils"></i>${escapeHtml(t("Outils"))}</span><span><i class="part-systeme"></i>${escapeHtml(t("Système"))}</span><span><i></i>${escapeHtml(t("Libre"))}</span></div>`;
}

export function memoryBannerHtml() {
  return '<div id="etabli-memoire" class="note memoire-etabli"></div>';
}

// « Ajouter un outil » : les outils pas encore installés, rangés par usage.
const GROUPS = [
  [t("Fichiers et souvenirs"), ["nextcloud", "immich", "paperless"]],
  [t("Films et séries"), ["jellyfin"]],
  [t("Atelier et maison"), ["atelier", "homebox"]],
  [t("Sécurité"), ["vaultwarden", "adguard-home", "antivirus"]],
  [t("Sauvegarde"), ["time-machine"]],
  [t("Veille"), ["changedetection"]],
];

/** Un outil pas encore installé : ce qu'il fait, ce qu'il prendrait en mémoire, et « Installer ». */
function offerCardHtml(tool, memory) {
  const remaining = memory.libre - tool.memoire;
  const tooMuch = !tool.integre && remaining < LOW_MEMORY;
  const cost = tool.integre
    ? t("Fait partie de Jabba : ne prend rien de plus.")
    : tooMuch
      ? t`Prend environ ${formatBytes(tool.memoire)} : il n'en resterait que ${formatBytes(Math.max(remaining, 0))}. Mets d'abord un autre outil en pause.`
      : t`Prend environ ${formatBytes(tool.memoire)} de mémoire : il en resterait ${formatBytes(remaining)}.`;
  return `<article class="carte pad outil offre" data-offre="${escapeHtml(tool.id)}">
      <div class="haut"><div class="titre-outil">${toolIcon({ id: tool.id, nom: tool.nom })}<div><h2>${escapeHtml(tool.nom)}</h2><div class="role">${escapeHtml(tool.role)}</div></div></div>
      <div class="haut-droite">${toolMenuHtml(tool)}</div></div>
      <div class="pied-offre"><p class="note${tooMuch ? " alerte-memoire" : ""}">${cost}</p>
      <button class="btn btn-p" type="button" data-activer="${escapeHtml(tool.id)}"${tooMuch ? " disabled" : ""}>${escapeHtml(tool.integre ? t("Ajouter") : t("Installer"))}</button></div>
      <div class="panneau-outil" data-panneau="${escapeHtml(tool.id)}" hidden></div>
    </article>`;
}

/** Les offres rangées par usage ; `extras` : des cartes déjà prêtes, par identifiant (l'antivirus). */
function offersHtml(off, memory, extras) {
  const cards = { ...extras };
  off.forEach((tool) => (cards[tool.id] = offerCardHtml(tool, memory)));
  const placed = new Set(GROUPS.flatMap(([, ids]) => ids));
  const others = Object.keys(cards).filter((id) => !placed.has(id));
  return [...GROUPS, [t("Autres"), others]]
    .map(([title, ids]) => {
      const html = ids.filter((id) => cards[id]).map((id) => cards[id]);
      return html.length
        ? `<h3 class="titre-groupe">${escapeHtml(title)}</h3><div class="grille g2">${html.join("")}</div>`
        : "";
    })
    .join("");
}

async function enable(tool, scope, reload) {
  const button = scope.querySelector(`[data-activer="${tool.id}"]`);
  button.disabled = true;
  button.textContent = t("Installation…");
  try {
    const task = await api(`/api/etabli/${encodeURIComponent(tool.id)}/installer`, {
      method: "POST",
      json: {},
    });
    if (task) await followTask(task); // un outil de l'appli s'ajoute tout de suite
    toast(t`${tool.nom} est installé`);
    reload();
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
    button.textContent = tool.integre ? t("Ajouter") : t("Installer");
  }
}

/** Remplit l'Établi : les outils de l'appli ajoutés vont dans « Mes outils » (`grid`), les autres dans
 * « Ajouter un outil » (`offers`, avec les cartes déjà prêtes de `extras`) ; puis la ligne de mémoire. */
export function bindCatalog({ grid, offers, extras = {} }, reload, onCards) {
  api("/api/etabli/catalogue")
    .then((result) => {
      const memory = result.memoire;
      const banner = $("#etabli-memoire");
      if (banner)
        banner.innerHTML =
          t`Mémoire du serveur : <strong>${formatBytes(memory.utilise)}</strong> utilisés sur ${formatBytes(memory.total)}, dont ${formatBytes(memory.outils)} pour les outils. Libre : <strong>${formatBytes(memory.libre)}</strong>.` +
          memoryBarHtml(memory);
      if (!grid || !offers) return;
      const builtInOn = result.outils
        .filter((tool) => tool.integre && tool.installe)
        .map((tool) => ({ ...tool, etat: "actif", modifiable: false }));
      const off = result.outils.filter((tool) => !tool.installe);
      grid.insertAdjacentHTML("beforeend", builtInOn.map((tool) => toolCardHtml(tool)).join(""));
      offers.innerHTML = offersHtml(off, memory, extras);
      offers.querySelectorAll("[data-activer]").forEach((button) => {
        button.onclick = () =>
          enable(
            off.find((tool) => tool.id === button.dataset.activer),
            offers,
            reload,
          );
      });
      if (onCards) onCards([...builtInOn, ...off]);
    })
    .catch(() => {});
}
