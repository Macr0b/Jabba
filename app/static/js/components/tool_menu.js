// Menu « ••• » des cartes de l'Établi : Redémarrer, Mettre à jour, Aide, Informations, et en dernier
// Désinstaller (en rouge). Aide et Informations s'ouvrent dans la carte elle-même (pas de fenêtre par-dessus).
//
// Sommaire :
//   partagé : toolMenuHtml, bindToolMenus, runToolAction
//   interne : uninstall
// AdGuard Home a en plus « Appareils » : le bloqueur de pubs, appareil par appareil (adblock_devices.js).
// Les outils dont Jabba a créé le compte ont « Identifiants » (tool_login.js).
// Ceux qui ont une page ont « Installer sur cet appareil » (install_as_app.js).
import { renderAdblockDevices } from "./adblock_devices.js";
import { bindMenus, menuHtml } from "./card_menu.js";
import { renderInstallAsApp } from "./install_as_app.js";
import { renderToolLogin } from "./tool_login.js";
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
  if (item.identifiants && item.etat) entries.push({ action: "identifiants", texte: t("Identifiants") });
  if (item.lien && item.etat && !item.systeme && item.id !== "nextcloud")
    entries.push({ action: "installer-appli", texte: t("Installer sur cet appareil") });
  if (item.modifiable && item.etat && item.etat !== "arrêté" && !item.systeme)
    entries.push({ action: "redemarrer", texte: t("Redémarrer") });
  if (item.modifiable && item.etat) entries.push({ action: "mettre-a-jour", texte: t("Mettre à jour") });
  entries.push({ action: "aide", texte: t("Aide") }, { action: "infos", texte: t("Informations") });
  if (item.installable !== false && canUninstall(item.id) && item.installe !== false)
    entries.push({ action: "desinstaller", texte: t("Désinstaller"), danger: true });
  return menuHtml(item.id, escapeHtml(t`Plus d'options pour ${item.nom}`), entries);
}

/** Redémarrer ou mettre à jour un outil, après confirmation ; l'écran se recharge ensuite. */
export async function runToolAction(item, action, reload) {
  const texts = {
    redemarrer: [t`Redémarrer ${item.nom} ?`, t("Il sera indisponible quelques secondes."), t("Redémarrer")],
    "mettre-a-jour": [
      t`Mettre à jour ${item.nom} ?`,
      t("Il sera indisponible pendant la mise à jour (1 à 5 minutes). Ses réglages et ses fichiers sont gardés."),
      t("Mettre à jour"),
    ],
  }[action];
  if (!(await confirmAction({ titre: texts[0], texte: texts[1], valider: texts[2] }))) return;
  try {
    await followTask(await api(`/api/outils/${encodeURIComponent(item.id)}/${action}`, { method: "POST" }));
  } catch (error) {
    toast(error.message, true);
  }
  reload();
}

async function uninstall(item, reload) {
  const ok = await confirmAction({
    titre: t`Désinstaller ${item.nom} ?`,
    texte: t(
      "L'outil est arrêté et retiré du serveur. Ses données (photos, mots de passe, sauvegardes, carnets) restent : le réinstaller les retrouve.",
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
    if (action === "redemarrer" || action === "mettre-a-jour") return runToolAction(item, action, reload);
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
    if (action === "installer-appli") {
      panel.dataset.contenu = action;
      panel.hidden = false;
      return renderInstallAsApp(panel, item);
    }
    if (action === "identifiants") {
      panel.dataset.contenu = action;
      panel.hidden = false;
      return renderToolLogin(panel, item);
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
