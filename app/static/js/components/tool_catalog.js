// Établi : la mémoire du serveur et les outils à activer en option (Vaultwarden, Immich, Time Machine…).
// Les fichiers d'installation sont fixés côté serveur (host/catalog.py) : ici, on choisit et on suit.
//
// Sommaire :
//   partagé : memoryBannerHtml, catalogHtml, bindCatalog, toolHint
//   interne : offerHtml, memoryAfter, builtInHtml, bindRemove
import { confirmAction, toast } from "./dialogs.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatBytes } from "../core/format.js";
import { t } from "../core/i18n.js";
import { followTask } from "../core/tasks.js";
import { toolIcon } from "../pages/home.js";

const LOW_MEMORY = 0.5 * 1024 ** 3; // en dessous, le serveur risque de ramer

export function memoryBannerHtml() {
  return '<div id="etabli-memoire" class="note memoire-etabli"></div>';
}

export function catalogHtml() {
  return '<div id="etabli-catalogue"></div>';
}

/** Ce qu'il resterait de mémoire libre si l'outil était activé. */
function memoryAfter(memory, tool) {
  return memory.libre - tool.memoire;
}

/** Comment se servir d'un outil en option qui n'a pas de page à ouvrir. */
export function toolHint(item) {
  if (item.id !== "time-machine") return "";
  return t`<p class="note">Sur le Mac : Réglages Système → Général → Time Machine → Ajouter un disque de sauvegarde → <strong>TimeMachine</strong>, compte <strong>timemachine</strong> et le mot de passe choisi à l'installation. Les sauvegardes vont dans le dossier TimeMachine du disque de données.</p>`;
}

function offerHtml(tool, memory) {
  const remaining = memoryAfter(memory, tool);
  const tooMuch = remaining < LOW_MEMORY;
  return `<article class="carte pad outil" data-offre="${escapeHtml(tool.id)}">
      <div class="haut"><div class="titre-outil">${toolIcon({ nom: tool.nom })}<div><h2>${escapeHtml(tool.nom)}</h2><div class="role">${escapeHtml(tool.role)}</div></div></div>
      <span class="pastille p-gris">${t("À activer")}</span></div>
      <p class="note${tooMuch ? " alerte-memoire" : ""}">${
        tooMuch
          ? t`Prend environ ${formatBytes(tool.memoire)} de mémoire : il n'en resterait que ${formatBytes(Math.max(remaining, 0))}. Désactive d'abord un autre outil.`
          : t`Prend environ ${formatBytes(tool.memoire)} de mémoire : il en resterait ${formatBytes(remaining)}.`
      }</p>
      ${
        tool.mot_de_passe
          ? t`<label class="champ"><span>Mot de passe à taper sur le Mac (15 caractères au moins)</span><input type="password" data-mot-de-passe="${escapeHtml(tool.id)}" autocomplete="new-password"></label>`
          : ""
      }
      <div class="actions"><button class="btn btn-p" type="button" data-installer="${escapeHtml(tool.id)}"${tooMuch ? " disabled" : ""}>${t("Activer")}</button></div>
    </article>`;
}

export function bindCatalog(reload) {
  api("/api/etabli/catalogue")
    .then((result) => {
      const memory = result.memoire;
      const banner = $("#etabli-memoire");
      if (banner)
        banner.innerHTML = t`Mémoire du serveur : <strong>${formatBytes(memory.utilise)}</strong> utilisés sur ${formatBytes(memory.total)}, dont ${formatBytes(memory.outils)} pour les outils. Libre : <strong>${formatBytes(memory.libre)}</strong>.`;
      const offers = result.outils.filter((tool) => !tool.installe);
      const builtIn = result.outils.filter((tool) => tool.integre && tool.installe);
      const place = $("#etabli-catalogue");
      if (!place) return;
      place.innerHTML =
        (builtIn.length
          ? t`<h2 class="titre-section">Dans l'appli</h2><div class="grille g2">${builtIn.map(builtInHtml).join("")}</div>`
          : "") +
        (offers.length
          ? t`<h2 class="titre-section">À activer en option</h2><div class="grille g2">${offers.map((tool) => offerHtml(tool, memory)).join("")}</div>`
          : "");
      bindRemove(place, reload);
      place.querySelectorAll("[data-installer]").forEach((button) => {
        button.onclick = async () => {
          const tool = offers.find((item) => item.id === button.dataset.installer);
          const field = place.querySelector(`[data-mot-de-passe="${tool.id}"]`);
          button.disabled = true;
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
            button.disabled = false;
          }
        };
      });
    })
    .catch(() => {});
  bindRemove(content, reload);
}

/** Un outil de l'appli activé : on l'ouvre comme un écran, ou on le retire (ses données restent). */
function builtInHtml(tool) {
  return `<article class="carte pad outil" data-integre="${escapeHtml(tool.id)}">
      <div class="haut"><div class="titre-outil">${toolIcon({ nom: tool.nom })}<div><h2>${escapeHtml(tool.nom)}</h2><div class="role">${escapeHtml(tool.role)}</div></div></div>
      <span class="pastille p-ok">${t("Actif")}</span></div>
      <div class="actions"><a class="btn btn-p" href="${escapeHtml(tool.page)}">${t("Ouvrir")}</a>
      <button class="btn" type="button" data-retirer="${escapeHtml(tool.id)}" data-nom="${escapeHtml(tool.nom)}">${t("Retirer")}</button></div>
    </article>`;
}

function bindRemove(scope, reload) {
  scope.querySelectorAll("[data-retirer]").forEach((button) => {
    button.onclick = async () => {
      const name = button.dataset.nom;
      const ok = await confirmAction({
        titre: t`Retirer ${name} ?`,
        texte: t(
          "L'outil est arrêté et retiré de l'Établi. Ses données (photos, mots de passe, sauvegardes) restent sur le serveur : le réactiver les retrouve.",
        ),
        valider: t("Retirer"),
        danger: true,
      });
      if (!ok) return;
      try {
        const task = await api(`/api/etabli/${encodeURIComponent(button.dataset.retirer)}/retirer`, { method: "POST" });
        if (task) await followTask(task);
        reload();
      } catch (error) {
        toast(error.message, true);
      }
    };
  });
}
