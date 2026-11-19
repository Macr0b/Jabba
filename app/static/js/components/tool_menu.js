// Menu « ••• » des cartes de l'Établi : Aide, Informations, Désinstaller.
// Aide et Informations s'ouvrent dans la carte elle-même (pas de fenêtre par-dessus).
//
// Sommaire :
//   partagé : toolMenuHtml, bindToolMenus
//   interne : uninstall
// AdGuard Home a en plus « Appareils » : le bloqueur de pubs, appareil par appareil (adblock_devices.js).
import { renderAdblockDevices } from "./adblock_devices.js";
import { bindMenus, menuHtml } from "./card_menu.js";
import { confirmAction, toast } from "./dialogs.js";
import { canUninstall, helpHtml, infoHtml, uninstallUrl } from "./tool_help.js";
import { api } from "../core/api.js";
import { escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { followTask } from "../core/tasks.js";

export function toolMenuHtml(item) {
  const entries = [];
  if (item.id === "adguard-home" && item.installe !== false && item.etat)
    entries.push({ action: "appareils", texte: t("Appareils") });
  entries.push({ action: "aide", texte: t("Aide") }, { action: "infos", texte: t("Informations") });
  if (item.installable !== false && canUninstall(item.id) && item.installe !== false)
    entries.push({ action: "desinstaller", texte: t("Désinstaller"), danger: true });
  return menuHtml(item.id, escapeHtml(t`Plus d'options pour ${item.nom}`), entries);
}

async function uninstall(item, reload) {
  const ok = await confirmAction({
    titre: t`Désinstaller ${item.nom} ?`,
    texte: t(
      "L'outil est arrêté et retiré du serveur. Ses données (photos, mots de passe, sauvegardes, carnets) restent : le rallumer les retrouve.",
    ),
    valider: t("Désinstaller"),
    danger: true,
  });
  if (!ok) return;
  try {
    const task = await api(uninstallUrl(item.id), { method: "POST" });
    if (task && task.id) await followTask(task);
    toast(t`${item.nom} est désinstallé`);
    reload();
  } catch (error) {
    toast(error.message, true);
  }
}

/** `items` : les outils affichés (ceux du serveur, ou ceux du catalogue), pour retrouver chaque carte. */
export function bindToolMenus(scope, items, versions, reload) {
  bindMenus(scope, async (action, id) => {
    const item = items.find((tool) => tool.id === id);
    if (!item) return;
    if (action === "desinstaller") return uninstall(item, reload);
    const panel = scope.querySelector(`[data-panneau="${CSS.escape(id)}"]`);
    if (!panel) return;
    if (!panel.hidden && panel.dataset.contenu === action) {
      panel.hidden = true;
      return;
    }
    if (action === "appareils") {
      panel.dataset.contenu = action;
      panel.hidden = false;
      return renderAdblockDevices(panel);
    }
    let usage = null;
    if (action === "infos") {
      usage = await api("/api/outils-conso")
        .then((all) => all[id])
        .catch(() => null);
    }
    const known = (versions.outils || {})[id];
    panel.innerHTML = action === "aide" ? helpHtml(id) : infoHtml(item, known && known.version, usage);
    panel.dataset.contenu = action;
    panel.hidden = false;
  });
}
