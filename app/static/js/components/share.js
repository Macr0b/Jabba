// Partager un dossier avec un rang ou un groupe (depuis Fichiers), et les noms des groupes à afficher.
//
// Sommaire :
//   partagé : groupLabel, folderTargets, shareFolder
import { chooseFromMenu, toast } from "./dialogs.js";
import { api } from "../core/api.js";

// Nom affiché d'un groupe : « Tous les membres », « Gérant et au-dessus », ou le nom du groupe.
export function groupLabel(groupId, cloud) {
  if (groupId === cloud.tous) return "Tous les membres";
  const rank = (cloud.rangs || []).find((entry) => entry.id === groupId);
  return rank ? `${rank.nom} et au-dessus` : groupId;
}

// À qui donner un dossier : tous, puis les rangs (du plus haut au plus bas), puis les groupes.
export function folderTargets(cloud) {
  return [
    ...cloud.groupes.filter((group) => group.id === cloud.tous),
    ...(cloud.rangs_actif ? [...cloud.rangs].reverse() : []),
    ...cloud.groupes.filter((group) => group.id !== cloud.tous),
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
    `Partager « ${name} » avec…`,
    folderTargets(cloud).map((group) => ({ texte: groupLabel(group.id, cloud), id: group.id })),
  );
  if (!target) return;
  const mode = await chooseFromMenu(`${target.texte} pourra…`, [
    { texte: "Voir seulement", id: "lecture" },
    { texte: "Voir et modifier", id: "ecriture" },
  ]);
  if (!mode) return;
  try {
    await api("/api/partages", { method: "POST", json: { chemin: path, groupe: target.id, mode: mode.id } });
    toast(`« ${name} » partagé avec ${target.texte}. Il apparaît dans leur Nextcloud.`);
  } catch (error) {
    toast(error.message, true);
  }
}
