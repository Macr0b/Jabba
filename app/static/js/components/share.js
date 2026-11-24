// Partager un dossier avec un rang ou un groupe (depuis Fichiers), et les noms des groupes à afficher.
//
// Sommaire :
//   partagé : groupLabel, folderTargets, shareFolder
import { chooseFromMenu, toast } from "./dialogs.js";
import { api } from "../core/api.js";
import { t } from "../core/i18n.js";

// Nom affiché d'un groupe : « Tous les membres », « Gérant et au-dessus », ou le nom du groupe.
export function groupLabel(groupId, cloud) {
  if (groupId === cloud.all) return t("Tous les membres");
  const rank = (cloud.ranks || []).find((entry) => entry.id === groupId);
  return rank ? t`${rank.name} et au-dessus` : groupId;
}

// À qui donner un dossier : tous, puis les rangs (du plus haut au plus bas), puis les groupes.
export function folderTargets(cloud) {
  return [
    ...cloud.groups.filter((group) => group.id === cloud.all),
    ...(cloud.ranks_enabled ? [...cloud.ranks].reverse() : []),
    ...cloud.groups.filter((group) => group.id !== cloud.all),
  ];
}

// « Partager avec… » : on choisit qui, puis voir seulement ou voir et modifier.
export async function shareFolder(path, name) {
  let cloud;
  try {
    cloud = await api("/api/nuage");
  } catch (error) {
    toast(error.message, true);
    return;
  }
  const target = await chooseFromMenu(
    t`Partager « ${name} » avec…`,
    folderTargets(cloud).map((group) => ({ text: groupLabel(group.id, cloud), id: group.id })),
  );
  if (!target) return;
  const mode = await chooseFromMenu(`${target.text} pourra…`, [
    { text: t("Voir seulement"), id: "lecture" },
    { text: t("Voir et modifier"), id: "ecriture" },
  ]);
  if (!mode) return;
  try {
    await api("/api/partages", { method: "POST", json: { path, group: target.id, mode: mode.id } });
    toast(t`« ${name} » partagé avec ${target.text}. Il apparaît dans leur Nextcloud.`);
  } catch (error) {
    toast(error.message, true);
  }
}
