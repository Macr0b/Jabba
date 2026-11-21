// Partage dans Nextcloud : dossiers communs, dossiers partagés, rangs et groupes.
//
// Sommaire :
//   partagé : bindSharing, chooseRank, loadSharedFolders
//
// `context` = {cloud, superAdmin, reload, act}, fourni par l'écran (pages/cloud.js).
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatGigabytes } from "../core/format.js";
import { t } from "../core/i18n.js";
import { chooseQuota } from "./cloud_accounts.js";
import { chooseFromMenu, confirmAction, toast } from "./dialogs.js";
import { folderTargets, groupLabel } from "./share.js";

export async function chooseRank(cloud, title, current) {
  const choices = [...cloud.rangs]
    .reverse()
    .map((rank) => ({ texte: rank.nom + (rank.id === current ? t(" (actuel)") : ""), id: rank.id }));
  choices.push({ texte: t("Aucun rang") + (current ? "" : t(" (actuel)")), id: null });
  return chooseFromMenu(title, choices);
}

// Les dossiers partagés (avec « Arrêter le partage »), chargés à part : l'écran s'affiche sans les attendre.
export async function loadSharedFolders(cloud) {
  const list = $("#partages-liste");
  if (!list) return;
  let shared;
  try {
    shared = await api("/api/partages");
  } catch (error) {
    list.innerHTML = `<p class="note">${escapeHtml(error.message)}</p>`;
    return;
  }
  list.innerHTML =
    shared.partages
      .map(
        (share) =>
          t`<div class="sauv-ligne"><span><strong>${escapeHtml(share.nom)}</strong><br><span class="note">${escapeHtml(share.chemin)} · ${escapeHtml(groupLabel(share.groupe, cloud))} : ${escapeHtml((shared.modes[share.mode] || "").toLowerCase())}</span></span><button class="btn btn-petit" data-arreter="${escapeHtml(share.id)}">Arrêter</button></div>`,
      )
      .join("") || t('<p class="note">Aucun pour l\'instant.</p>');
  list.querySelectorAll("[data-arreter]").forEach((button) =>
    button.addEventListener("click", async () => {
      const share = shared.partages.find((item) => item.id === button.dataset.arreter);
      const confirmed = await confirmAction({
        titre: t`Arrêter le partage de « ${share.nom} » ?`,
        texte: t("Les personnes n'y auront plus accès. Les fichiers ne bougent pas."),
        valider: t("Arrêter"),
        danger: true,
      });
      if (!confirmed) return;
      try {
        await api(`/api/partages/${encodeURIComponent(share.id)}/arreter`, { method: "POST" });
        toast(t("Partage arrêté"));
      } catch (error) {
        toast(error.message, true);
      }
      loadSharedFolders(cloud);
    }),
  );
}

export function bindSharing(context) {
  const { cloud, superAdmin, reload, act } = context;
  loadSharedFolders(cloud); // à part : l'écran s'affiche sans attendre cette liste
  // dossiers communs
  const chooseMode = (title) =>
    chooseFromMenu(
      title,
      Object.entries(cloud.modes).map(([key, item]) => ({ texte: `${item.nom} : ${item.detail}`, mode: key })),
    );
  $("#nuage-dossier").onclick = async () => {
    const name = await confirmAction({
      titre: t("Nouveau dossier commun"),
      texte: t("Son nom, tel qu'il apparaîtra dans Nextcloud."),
      champ: "",
      valider: t("Suivant"),
    });
    if (!name) return;
    const item = await chooseFromMenu(
      t("Pour qui ?"),
      folderTargets(cloud).map((item) => ({ texte: groupLabel(item.id, cloud), id: item.id })),
    );
    if (!item) return;
    const entry = await chooseMode(`${groupLabel(item.id, cloud)} pourra…`);
    if (!entry) return;
    if (
      await act(
        "/api/nuage/dossiers",
        { action: "creer", nom: name, groupe: item.id, mode: entry.mode },
        t`Dossier « ${name} » créé`,
      )
    )
      reload();
  };
  content.querySelectorAll("[data-dossier]").forEach((element) =>
    element.addEventListener("click", async () => {
      const item = cloud.dossiers.find((item) => item.id === +element.dataset.dossier);
      const choice = await chooseFromMenu(item.nom, [
        { texte: t("Qui y a accès"), f: "acces" },
        { texte: t`Taille maximale (${item.quota ? formatGigabytes(item.quota) : "aucune"})`, f: "quota" },
        { texte: t("Renommer"), f: "renommer" },
        { texte: t("Supprimer le dossier"), f: "supprimer", danger: true },
      ]);
      if (!choice) return;
      const body = { id: item.id };
      if (choice.f === "acces") {
        const entry = await chooseFromMenu(
          t`Accès à « ${item.nom} »`,
          folderTargets(cloud).map((entry) => {
            const currentAccess = item.acces.find((other) => other.groupe === entry.id);
            return {
              texte: `${groupLabel(entry.id, cloud)} : ${currentAccess ? cloud.modes[currentAccess.mode].nom.toLowerCase() : t("pas d'accès")}`,
              id: entry.id,
              a: currentAccess,
            };
          }),
        );
        if (!entry) return;
        const choices = Object.entries(cloud.modes).map(([key, item]) => ({
          texte: `${item.nom} : ${item.detail}`,
          mode: key,
        }));
        if (entry.a) choices.push({ texte: t("Pas d'accès"), mode: null, danger: true });
        const choice = await chooseFromMenu(`${groupLabel(entry.id, cloud)} pourra…`, choices);
        if (!choice) return;
        Object.assign(body, { action: "acces", groupe: entry.id, mode: choice.mode });
      } else if (choice.f === "quota") {
        const value = await chooseQuota(
          t`Taille maximale de « ${item.nom} »`,
          item.quota,
          { plafond_go: 100000 },
          true,
        );
        if (value === undefined) return;
        Object.assign(body, { action: "quota", go: value });
      } else if (choice.f === "renommer") {
        const name = await confirmAction({ titre: t("Renommer"), champ: item.nom, valider: t("Renommer") });
        if (!name || name === item.nom) return;
        Object.assign(body, { action: "renommer", nom: name });
      } else {
        const value = await confirmAction({
          titre: t`Supprimer « ${item.nom} » ?`,
          texte: t("Le dossier et TOUT son contenu sont effacés pour tout le monde. Pour confirmer, tape SUPPRIMER."),
          champ: "",
          valider: t("Supprimer"),
          danger: true,
        });
        if (value !== "SUPPRIMER") {
          if (value !== null) toast(t("Pas confirmé : rien n'a été supprimé."), true);
          return;
        }
        body.action = "supprimer";
      }
      if (await act("/api/nuage/dossiers", body, t("C'est fait"))) reload();
    }),
  );

  // rangs (compte du serveur)
  const ranks = (json, ok) => act("/api/nuage/rangs", json, ok).then((result) => result && reload());
  const rankAction = $("#rangs-actif");
  if (rankAction)
    rankAction.onclick = async () => {
      if (
        cloud.rangs_actif &&
        !(await confirmAction({
          titre: t("Désactiver les rangs ?"),
          texte: t(
            "Chacun garde son rang, mais les dossiers donnés à un rang ne sont plus visibles jusqu'à la réactivation.",
          ),
          valider: t("Désactiver"),
          danger: true,
        }))
      )
        return;
      ranks(
        { action: "activer", actif: !cloud.rangs_actif },
        cloud.rangs_actif ? t("Rangs désactivés") : t("Rangs activés"),
      );
    };
  const rankName = $("#rang-nouveau");
  if (rankName)
    rankName.onclick = async () => {
      const name = await confirmAction({
        titre: t("Nouveau rang"),
        texte: t("Il se place tout en haut ; tu pourras le descendre ensuite."),
        champ: "",
        valider: t("Créer"),
      });
      if (name) ranks({ action: "creer", nom: name }, t`Rang « ${name} » créé`);
    };
  content.querySelectorAll("[data-rang]").forEach((element) =>
    element.addEventListener("click", async () => {
      const rank = cloud.rangs.find((item) => item.id === element.dataset.rang);
      const index = cloud.rangs.indexOf(rank);
      const choices = [{ texte: t("Renommer"), f: "renommer" }];
      if (index < cloud.rangs.length - 1) choices.push({ texte: t("Monter d'un cran"), f: "haut" });
      if (index > 0) choices.push({ texte: t("Descendre d'un cran"), f: "bas" });
      choices.push({ texte: t("Supprimer le rang"), f: "supprimer", danger: true });
      const choice = await chooseFromMenu(rank.nom, choices);
      if (!choice) return;
      if (choice.f === "renommer") {
        const name = await confirmAction({ titre: t("Renommer le rang"), champ: rank.nom, valider: t("Renommer") });
        if (name && name !== rank.nom) ranks({ action: "renommer", id: rank.id, nom: name }, t("Rang renommé"));
      } else if (choice.f === "supprimer") {
        if (
          await confirmAction({
            titre: t`Supprimer le rang ${rank.nom} ?`,
            texte: t`Ses ${rank.membres} membre(s) descendent d'un rang. Les dossiers donnés à ce rang ne leur sont plus donnés.`,
            valider: t("Supprimer"),
            danger: true,
          })
        )
          ranks({ action: "supprimer", id: rank.id }, t("Rang supprimé"));
      } else ranks({ action: "deplacer", id: rank.id, sens: choice.f }, t("Ordre changé"));
    }),
  );

  // groupes
  $("#nuage-groupe").onclick = async () => {
    const name = await confirmAction({
      titre: t("Nouveau groupe"),
      texte: t("Par exemple : Fablab, Famille…"),
      champ: "",
      valider: t("Créer"),
    });
    if (name && (await act("/api/nuage/groupes", { nom: name }, t`Groupe « ${name} » créé`))) reload();
  };
  content.querySelectorAll("[data-groupe]").forEach((element) =>
    element.addEventListener("click", async () => {
      const item = cloud.groupes.find((item) => item.id === element.dataset.groupe);
      const names = item.membres.map((id) => (cloud.comptes.find((item) => item.id === id) || { nom: id }).nom);
      if (item.id === cloud.tous)
        return confirmAction({
          titre: t("Tous les membres"),
          texte: names.join(", ") || t("Personne pour l'instant."),
          valider: "OK",
        });
      const choice = await chooseFromMenu(item.id, [
        { texte: t("Ajouter quelqu'un"), f: "ajouter" },
        ...(names.length ? [{ texte: t("Retirer quelqu'un"), f: "retirer" }] : []),
        { texte: t("Supprimer le groupe"), f: "supprimer", danger: true },
      ]);
      if (!choice) return;
      if (choice.f === "supprimer") {
        if (
          !(await confirmAction({
            titre: t`Supprimer le groupe ${item.id} ?`,
            texte: t(
              "Les comptes et les fichiers restent. Ses membres perdent l'accès aux dossiers communs donnés à ce groupe.",
            ),
            valider: t("Supprimer"),
            danger: true,
          }))
        )
          return;
        if (await act("/api/nuage/groupes", { nom: item.id, supprimer: true }, t("Groupe supprimé"))) reload();
        return;
      }
      const member = choice.f === "ajouter";
      const list = cloud.comptes.filter(
        (entry) => entry.etat !== "attente" && item.membres.includes(entry.id) !== member,
      );
      if (!list.length) return toast(member ? t("Tout le monde en fait déjà partie.") : t("Personne à retirer."));
      const entry = await chooseFromMenu(
        member ? t`Ajouter à ${item.id}` : t`Retirer de ${item.id}`,
        list.map((item) => ({ texte: `${item.nom} (${item.id})`, id: item.id })),
      );
      if (
        entry &&
        (await act(
          `/api/nuage/comptes/${encodeURIComponent(entry.id)}`,
          { action: "groupe", groupe: item.id, dedans: member },
          t("C'est fait"),
        ))
      )
        reload();
    }),
  );
}
