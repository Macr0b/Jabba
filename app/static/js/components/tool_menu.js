// Menu « ••• » des cartes de l'Établi : Redémarrer, Mettre à jour, Aide, Informations, et en dernier
// Désinstaller (en rouge). Aide et Informations s'ouvrent dans la carte elle-même (pas de fenêtre par-dessus).
//
// Sommaire :
//   partagé : toolMenuHtml, bindToolMenus, runToolAction
//   interne : uninstall, removeLeftover
// AdGuard Home a en plus « Appareils » : le bloqueur de pubs, appareil par appareil (adblock_devices.js).
// Les outils dont Jabba a créé le compte ont « Identifiants » (tool_login.js).
// Ceux qui ont une page ont « Installer sur cet appareil » (install_as_app.js) ; Nextcloud a
// « Connecter un ordinateur ou un téléphone » (connect_device.js), son dossier synchronisé façon Dropbox.
import { renderAdblockDevices } from "./adblock_devices.js";
import { renderConnectDevice } from "./connect_device.js";
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
  if (item.id === "adguard-home" && item.installed !== false && item.state)
    entries.push({ action: "appareils", text: t("Appareils") });
  if (item.credentials && item.state) entries.push({ action: "identifiants", text: t("Identifiants") });
  if (item.id === "nextcloud" && item.state && item.link)
    entries.push({ action: "connecter", text: t("Connecter un ordinateur ou un téléphone") });
  if (item.link && item.state && !item.system && item.id !== "nextcloud")
    entries.push({ action: "installer-appli", text: t("Installer sur cet appareil") });
  if (item.editable && item.state && item.state !== "arrêté" && !item.system)
    entries.push({ action: "redemarrer", text: t("Redémarrer") });
  if (item.editable && item.state) entries.push({ action: "mettre-a-jour", text: t("Mettre à jour") });
  entries.push({ action: "aide", text: t("Aide") }, { action: "infos", text: t("Informations") });
  if (item.installable !== false && canUninstall(item.id) && item.installed !== false)
    entries.push({ action: "desinstaller", text: t("Désinstaller"), danger: true });
  if (item.leftover) entries.push({ action: "retirer-ancien", text: t("Désinstaller"), danger: true });
  return menuHtml(item.id, escapeHtml(t`Plus d'options pour ${item.name}`), entries);
}

/** Redémarrer ou mettre à jour un outil, après confirmation ; l'écran se recharge ensuite. */
export async function runToolAction(item, action, reload) {
  const texts = {
    redemarrer: [t`Redémarrer ${item.name} ?`, t("Il sera indisponible quelques secondes."), t("Redémarrer")],
    "mettre-a-jour": [
      t`Mettre à jour ${item.name} ?`,
      t("Il sera indisponible pendant la mise à jour (1 à 5 minutes). Ses réglages et ses fichiers sont gardés."),
      t("Mettre à jour"),
    ],
  }[action];
  if (!(await confirmAction({ title: texts[0], text: texts[1], confirmLabel: texts[2] }))) return;
  try {
    await followTask(await api(`/api/outils/${encodeURIComponent(item.id)}/${action}`, { method: "POST" }));
  } catch (error) {
    toast(error.message, true);
  }
  reload();
}

async function uninstall(item, reload) {
  const ok = await confirmAction({
    title: t`Désinstaller ${item.name} ?`,
    text: t(
      "L'outil est arrêté et retiré du serveur. Ses données (photos, mots de passe, sauvegardes, carnets) restent : le réinstaller les retrouve.",
    ),
    confirmLabel: t("Désinstaller"),
    danger: true,
  });
  if (!ok) return;
  try {
    const task = await api(uninstallUrl(item.id), { method: "POST" });
    if (task && task.id) await followTask(task);
    toast(t`${item.name} est désinstallé`);
    reload();
  } catch (error) {
    toast(error.message, true);
  }
}

// Un outil d'une ancienne version de Jabba (retiré du catalogue, comme ntfy) : retiré en entier.
async function removeLeftover(item, reload) {
  const ok = await confirmAction({
    title: t`Désinstaller ${item.name} ?`,
    text: t(
      "Cet outil ne fait plus partie de Jabba. Il est arrêté et retiré du serveur avec ses données et son adresse.",
    ),
    confirmLabel: t("Désinstaller"),
    danger: true,
  });
  if (!ok) return;
  try {
    await followTask(await api(`/api/etabli/${encodeURIComponent(item.id)}/retirer-ancien`, { method: "POST" }));
    toast(t`${item.name} est désinstallé`);
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
    if (action === "retirer-ancien") return removeLeftover(item, reload);
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
    if (action === "connecter") {
      panel.dataset.contenu = action;
      panel.hidden = false;
      return renderConnectDevice(panel, item);
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
    const known = (versions.tools || {})[id];
    panel.innerHTML = action === "aide" ? helpHtml(id) : infoHtml(item, known && known.version, usage);
    panel.dataset.contenu = action;
    panel.hidden = false;
  });
}
