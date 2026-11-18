// Établi : la mémoire du serveur, et les outils qu'on active d'un geste (Vaultwarden, Immich, Time Machine,
// Mémoire d'atelier). Ils apparaissent avec les autres outils, interrupteur éteint : l'allumer installe
// l'outil (fichiers fixés côté serveur, host/catalog.py). Une fois installé, l'interrupteur habituel
// l'arrête ou le relance, sans jamais toucher à ses données.
//
// Sommaire :
//   partagé : memoryBannerHtml, bindCatalog, toolHint
//   interne : offCardHtml, builtInCardHtml, enable, disableBuiltIn
import { toast } from "./dialogs.js";
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { formatBytes } from "../core/format.js";
import { t } from "../core/i18n.js";
import { followTask } from "../core/tasks.js";
import { toolIcon } from "../pages/home.js";
import { toolMenuHtml } from "./tool_menu.js";

const LOW_MEMORY = 0.5 * 1024 ** 3; // en dessous, le serveur risque de ramer

export function memoryBannerHtml() {
  return '<div id="etabli-memoire" class="note memoire-etabli"></div>';
}

/** Comment se servir d'un outil qui n'a pas de page à ouvrir. */
export function toolHint(item) {
  if (item.id !== "time-machine") return "";
  return t`<p class="note">Sur le Mac : Réglages Système → Général → Time Machine → Ajouter un disque de sauvegarde → <strong>TimeMachine</strong>, compte <strong>timemachine</strong> et le mot de passe choisi à l'activation. Les sauvegardes vont dans le dossier TimeMachine du disque de données.</p>`;
}

/** Un outil pas encore installé : éteint, avec ce qu'il prendrait en mémoire. */
function offCardHtml(tool, memory) {
  const remaining = memory.libre - tool.memoire;
  const tooMuch = remaining < LOW_MEMORY;
  return `<article class="carte pad outil eteint" data-offre="${escapeHtml(tool.id)}">
      <div class="haut"><div class="titre-outil">${toolIcon({ nom: tool.nom })}<div><h2>${escapeHtml(tool.nom)}</h2><div class="role">${escapeHtml(tool.role)}</div></div></div>
      <div class="haut-droite"><button class="interrupteur" role="switch" aria-checked="false" aria-label="${escapeHtml(t("Activer") + " " + tool.nom)}" data-activer="${escapeHtml(tool.id)}"${tooMuch ? " disabled" : ""}><span></span></button>${toolMenuHtml(tool)}</div></div>
      ${
        tool.integre
          ? ""
          : `<p class="note${tooMuch ? " alerte-memoire" : ""}">${
              tooMuch
                ? t`Prend environ ${formatBytes(tool.memoire)} de mémoire : il n'en resterait que ${formatBytes(Math.max(remaining, 0))}. Désactive d'abord un autre outil.`
                : t`Prend environ ${formatBytes(tool.memoire)} de mémoire : il en resterait ${formatBytes(remaining)}.`
            }</p>`
      }
      ${
        tool.mot_de_passe
          ? t`<label class="champ"><span>${escapeHtml(tool.mot_de_passe_texte)} (15 caractères au moins)</span><input type="password" data-mot-de-passe="${escapeHtml(tool.id)}" autocomplete="new-password"></label>`
          : ""
      }
      <div class="panneau-outil" data-panneau="${escapeHtml(tool.id)}" hidden></div>
    </article>`;
}

/** Un outil de l'appli activé (Mémoire d'atelier) : on l'ouvre comme un écran ; l'éteindre garde ses données. */
function builtInCardHtml(tool) {
  return `<article class="carte pad outil" data-integre="${escapeHtml(tool.id)}">
      <div class="haut"><div class="titre-outil">${toolIcon({ nom: tool.nom })}<div><h2>${escapeHtml(tool.nom)}</h2><div class="role">${escapeHtml(tool.role)}</div></div></div>
      <div class="haut-droite"><button class="interrupteur" role="switch" aria-checked="true" aria-label="${escapeHtml(t("Désactiver") + " " + tool.nom)}" data-eteindre="${escapeHtml(tool.id)}"><span></span></button>${toolMenuHtml(tool)}</div></div>
      <div class="actions"><a class="btn btn-p" href="${escapeHtml(tool.page)}">${t("Ouvrir")}</a></div>
      <div class="panneau-outil" data-panneau="${escapeHtml(tool.id)}" hidden></div>
    </article>`;
}

async function enable(tool, grid, reload) {
  const field = grid.querySelector(`[data-mot-de-passe="${tool.id}"]`);
  const toggle = grid.querySelector(`[data-activer="${tool.id}"]`);
  toggle.setAttribute("aria-checked", "true");
  toggle.disabled = true;
  try {
    const task = await api(`/api/etabli/${encodeURIComponent(tool.id)}/installer`, {
      method: "POST",
      json: { mot_de_passe: field ? field.value : null },
    });
    if (task) await followTask(task); // un outil de l'appli s'active tout de suite
    toast(t`${tool.nom} est activé`);
    reload();
  } catch (error) {
    toast(error.message, true);
    toggle.setAttribute("aria-checked", "false");
    toggle.disabled = false;
  }
}

async function disableBuiltIn(toolId, reload) {
  try {
    await api(`/api/etabli/${encodeURIComponent(toolId)}/retirer`, { method: "POST" });
    reload();
  } catch (error) {
    toast(error.message, true);
  }
}

/** Ajoute les outils à activer dans la grille de l'Établi (`grid`), et la ligne de mémoire. */
export function bindCatalog(grid, reload, onCards) {
  api("/api/etabli/catalogue")
    .then((result) => {
      const memory = result.memoire;
      const banner = $("#etabli-memoire");
      if (banner)
        banner.innerHTML = t`Mémoire du serveur : <strong>${formatBytes(memory.utilise)}</strong> utilisés sur ${formatBytes(memory.total)}, dont ${formatBytes(memory.outils)} pour les outils. Libre : <strong>${formatBytes(memory.libre)}</strong>.`;
      if (!grid) return;
      const builtInOn = result.outils.filter((tool) => tool.integre && tool.installe);
      const off = result.outils.filter((tool) => !tool.installe);
      grid.insertAdjacentHTML(
        "beforeend",
        builtInOn.map(builtInCardHtml).join("") + off.map((tool) => offCardHtml(tool, memory)).join(""),
      );
      grid.querySelectorAll("[data-activer]").forEach((toggle) => {
        toggle.onclick = () =>
          enable(
            off.find((tool) => tool.id === toggle.dataset.activer),
            grid,
            reload,
          );
      });
      grid.querySelectorAll("[data-eteindre]").forEach((toggle) => {
        toggle.onclick = () => disableBuiltIn(toggle.dataset.eteindre, reload);
      });
      if (onCards) onCards([...builtInOn, ...off]);
    })
    .catch(() => {});
}
