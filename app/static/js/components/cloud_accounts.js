// Comptes Nextcloud : valider une inscription, régler un compte (rang, espace, mot de passe…), en ajouter.
//
// Sommaire :
//   partagé : bindAccounts, bindCloudSettings, chooseQuota
//   interne : showCloudPassword
//
// `context` = {cloud, superAdmin, reload, act}, fourni par l'écran (pages/cloud.js).
import { formBody } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatGigabytes } from "../core/format.js";
import { t } from "../core/i18n.js";
import { chooseFromMenu, confirmAction, dialogElement, dialogForm, toast } from "./dialogs.js";
import { chooseRank } from "./cloud_sharing.js";

export async function chooseQuota(title, current, cloud, superAdmin, unlimited = true) {
  const sizes = [1, 2, 5, 10, 20, 50, 100, 200].filter((value) => superAdmin || value <= cloud.plafond_go);
  const choices = sizes.map((value) => ({
    texte: `${value} Go${current === value * 1073741824 ? t(" (actuel)") : ""}`,
    go: value,
  }));
  choices.push({ texte: t("Autre…"), autre: true });
  if (superAdmin && unlimited) choices.push({ texte: t("Illimité"), go: null });
  const item = await chooseFromMenu(title + (superAdmin ? "" : t` (plafond : ${cloud.plafond_go} Go)`), choices);
  if (!item) return undefined;
  if (!item.autre) return item.go;
  const value = await confirmAction({ titre: title, texte: t("En Go (par exemple 7,5)."), champ: "" });
  const entry = value && parseFloat(value.replace(",", "."));
  return entry > 0 ? entry : undefined;
}

export function bindAccounts(context) {
  const { cloud, superAdmin, reload, act } = context;
  // à valider
  content.querySelectorAll("[data-valider]").forEach((element) =>
    element.addEventListener("click", async () => {
      let rank = null;
      if (cloud.rangs_actif && cloud.rangs.length) {
        const chosen = await chooseRank(cloud, t`Rang de ${element.dataset.valider}`, null);
        if (!chosen) return;
        rank = chosen.id;
      }
      if (
        await act(
          `/api/nuage/comptes/${encodeURIComponent(element.dataset.valider)}`,
          { action: "valider", rang: rank },
          t("Compte validé"),
        )
      )
        reload();
    }),
  );
  content.querySelectorAll("[data-refuser]").forEach((element) =>
    element.addEventListener("click", async () => {
      if (
        !(await confirmAction({
          titre: t`Refuser ${element.dataset.refuser} ?`,
          texte: t("La demande est supprimée. La personne pourra en refaire une."),
          valider: t("Refuser"),
          danger: true,
        }))
      )
        return;
      if (
        await act(
          `/api/nuage/comptes/${encodeURIComponent(element.dataset.refuser)}`,
          { action: "refuser" },
          t("Demande refusée"),
        )
      )
        reload();
    }),
  );

  // un compte
  content.querySelectorAll("[data-compte]").forEach((element) =>
    element.addEventListener("click", async () => {
      const item = cloud.comptes.find((item) => item.id === element.dataset.compte);
      const url = `/api/nuage/comptes/${encodeURIComponent(item.id)}`;
      const choices = [
        { texte: t`Changer l'espace (${formatGigabytes(item.quota)})`, f: "espace" },
        { texte: t("Groupes"), f: "groupes" },
      ];
      if (superAdmin && cloud.rangs_actif && cloud.rangs.length)
        choices.unshift({
          texte: t`Rang (${(cloud.rangs.find((rank) => rank.id === item.rang_nuage) || { nom: "aucun" }).nom})`,
          f: "rang",
        });
      if (superAdmin) {
        choices.push({ texte: t("Nouveau mot de passe"), f: "mdp" });
        if (!item.admin)
          choices.push(
            { texte: item.etat === "suspendu" ? t("Réactiver") : t("Suspendre"), f: "suspendre" },
            { texte: t("Supprimer le compte"), f: "supprimer", danger: true },
          );
      }
      const choice = await chooseFromMenu(`${item.nom} (${item.id})`, choices);
      if (!choice) return;
      if (choice.f === "rang") {
        const rank = await chooseRank(cloud, t`Rang de ${item.nom}`, item.rang_nuage);
        if (
          rank &&
          rank.id !== item.rang_nuage &&
          (await act(
            url,
            { action: "rang", rang: rank.id },
            `${item.nom} : ${rank.id ? rank.texte.replace(" (actuel)", "") : t("aucun rang")}`,
          ))
        )
          reload();
      } else if (choice.f === "espace") {
        const value = await chooseQuota(t`Espace de ${item.nom}`, item.quota, cloud, superAdmin);
        if (
          value !== undefined &&
          (await act(
            url,
            { action: "quota", go: value },
            t`Espace de ${item.nom} : ${value == null ? t("illimité") : String(value).replace(".", ",") + " Go"}`,
          ))
        )
          reload();
      } else if (choice.f === "groupes") {
        const entry = await chooseFromMenu(
          t`Groupes de ${item.nom}`,
          cloud.groupes
            .filter((item) => item.id !== cloud.tous)
            .map((entry) => ({ texte: (item.groupes.includes(entry.id) ? "✓ " : "") + entry.id, id: entry.id })),
        );
        if (!entry) return;
        const member = !item.groupes.includes(entry.id);
        if (
          await act(
            url,
            { action: "groupe", groupe: entry.id, dedans: member },
            member ? t`${item.nom} ajouté à ${entry.id}` : t`${item.nom} retiré de ${entry.id}`,
          )
        )
          reload();
      } else if (choice.f === "mdp") {
        if (
          !(await confirmAction({
            titre: t`Nouveau mot de passe pour ${item.nom} ?`,
            texte: t("L'ancien ne marchera plus. Les applis Nextcloud de la personne devront se reconnecter."),
            valider: t("Changer"),
          }))
        )
          return;
        const result = await act(url, { action: "mdp" });
        if (result) await showCloudPassword(item, result.mdp);
      } else if (choice.f === "suspendre") {
        const active = item.etat === "suspendu";
        if (
          !active &&
          !(await confirmAction({
            titre: t`Suspendre ${item.nom} ?`,
            texte: t("La personne ne peut plus se connecter. Ses fichiers sont gardés."),
            valider: t("Suspendre"),
            danger: true,
          }))
        )
          return;
        if (
          await act(url, { action: "suspendre", actif: active }, active ? t("Compte réactivé") : t("Compte suspendu"))
        )
          reload();
      } else if (choice.f === "supprimer") {
        const value = await confirmAction({
          titre: t`Supprimer ${item.nom} ?`,
          texte: t`Le compte et TOUS ses fichiers sont effacés. Pour confirmer, tape ${item.id}.`,
          champ: "",
          valider: t("Supprimer"),
          danger: true,
        });
        if (value !== item.id) {
          if (value !== null) toast(t("Pas confirmé : rien n'a été supprimé."), true);
          return;
        }
        if (await act(url, { action: "supprimer" }, t("Compte supprimé"))) reload();
      }
    }),
  );

  // ajouter
  const addButton = $("#nuage-ajouter");
  if (addButton)
    addButton.onclick = async () => {
      const answers = await formBody({
        titre: t("Ajouter un compte"),
        texte: t("Pour quelqu'un qui ne passe pas par l'inscription. Un mot de passe provisoire s'affichera."),
        champs: [
          { nom: "nom", label: t("Prénom et nom") },
          { nom: "id", label: t("Identifiant (minuscules, sans espace)") },
          { nom: "email", label: t("E-mail (facultatif)"), type: "email" },
          ...(cloud.rangs_actif && cloud.rangs.length
            ? [
                {
                  nom: "rang",
                  label: t("Rang"),
                  valeur: cloud.rangs[0].id,
                  options: [...[...cloud.rangs].reverse().map((item) => [item.id, item.nom]), ["", t("Aucun rang")]],
                },
              ]
            : []),
        ],
        valider: t("Créer"),
      });
      if (!answers || answers === "secondaire") return;
      const result = await act("/api/nuage/comptes", {
        id: answers.id.trim().toLowerCase(),
        nom: answers.nom,
        email: answers.email,
        rang: answers.rang || null,
      });
      if (result) {
        await showCloudPassword({ nom: answers.nom || answers.id, id: answers.id.trim().toLowerCase() }, result.mdp);
        reload();
      }
    };
}

// Réglages (super admin) : espace des nouveaux comptes, plafond par compte.
export function bindCloudSettings(context) {
  const { cloud, superAdmin, reload, act } = context;
  $("#nuage-defaut").onclick = async () => {
    const value = await chooseQuota(t("Espace des nouveaux comptes"), null, cloud, false, false);
    if (
      value != null &&
      (await act(
        "/api/nuage/reglages",
        { quota_defaut_go: value },
        t`Nouveaux comptes : ${String(value).replace(".", ",")} Go`,
      ))
    )
      reload();
  };
  $("#nuage-plafond").onclick = async () => {
    const value = await confirmAction({
      titre: t("Plafond par compte"),
      texte: t("En Go. Un admin ne pourra pas donner plus à un compte."),
      champ: String(cloud.plafond_go),
      valider: t("Changer"),
    });
    const item = value && parseFloat(value.replace(",", "."));
    if (item > 0 && (await act("/api/nuage/reglages", { plafond_go: item }, t`Plafond : ${value} Go`))) reload();
  };
}

async function showCloudPassword(item, password) {
  await new Promise((resolve) => {
    dialogForm.innerHTML = t`<h2>Mot de passe Nextcloud</h2>
      <p>Donne-le à <strong>${escapeHtml(item.nom)}</strong> (identifiant <strong>${escapeHtml(item.id)}</strong>). Il ne sera plus affiché. Conseille-lui de le changer dans Nextcloud (Paramètres personnels, Sécurité).</p>
      <p class="code-secours">${escapeHtml(password)}</p>
      <div class="actions"><button class="btn" type="button" id="copier-mdp">Copier</button><button class="btn btn-p" value="ok" type="submit">C'est noté</button></div>`;
    dialogElement.onclose = () => resolve();
    dialogElement.showModal();
    $("#copier-mdp").onclick = async () => {
      try {
        await navigator.clipboard.writeText(password);
        toast(t("Copié"));
      } catch (_) {
        toast(t("Copie impossible : sélectionne-le à la main."), true);
      }
    };
  });
}
