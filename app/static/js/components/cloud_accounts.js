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
  const sizes = [1, 2, 5, 10, 20, 50, 100, 200].filter((value) => superAdmin || value <= cloud.cap_gb);
  const choices = sizes.map((value) => ({
    text: `${value} Go${current === value * 1073741824 ? t(" (actuel)") : ""}`,
    go: value,
  }));
  choices.push({ text: t("Autre…"), other: true });
  if (superAdmin && unlimited) choices.push({ text: t("Illimité"), go: null });
  const item = await chooseFromMenu(title + (superAdmin ? "" : t` (plafond : ${cloud.cap_gb} Go)`), choices);
  if (!item) return undefined;
  if (!item.other) return item.go;
  const value = await confirmAction({ title, text: t("En Go (par exemple 7,5)."), field: "" });
  const entry = value && parseFloat(value.replace(",", "."));
  return entry > 0 ? entry : undefined;
}

export function bindAccounts(context) {
  const { cloud, superAdmin, reload, act } = context;
  // à valider
  content.querySelectorAll("[data-valider]").forEach((element) =>
    element.addEventListener("click", async () => {
      let rank = null;
      if (cloud.ranks_enabled && cloud.ranks.length) {
        const chosen = await chooseRank(cloud, t`Rang de ${element.dataset.valider}`, null);
        if (!chosen) return;
        rank = chosen.id;
      }
      if (
        await act(
          `/api/nuage/comptes/${encodeURIComponent(element.dataset.valider)}`,
          { action: "valider", rank },
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
          title: t`Refuser ${element.dataset.refuser} ?`,
          text: t("La demande est supprimée. La personne pourra en refaire une."),
          confirmLabel: t("Refuser"),
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
      const item = cloud.accounts.find((item) => item.id === element.dataset.compte);
      const url = `/api/nuage/comptes/${encodeURIComponent(item.id)}`;
      const choices = [
        { text: t`Changer l'espace (${formatGigabytes(item.quota)})`, f: "espace" },
        { text: t("Groupes"), f: "groupes" },
      ];
      if (superAdmin && cloud.ranks_enabled && cloud.ranks.length)
        choices.unshift({
          text: t`Rang (${(cloud.ranks.find((rank) => rank.id === item.cloud_rank) || { name: "aucun" }).name})`,
          f: "rang",
        });
      if (superAdmin) {
        choices.push({ text: t("Nouveau mot de passe"), f: "mdp" });
        if (!item.admin)
          choices.push(
            { text: item.state === "suspendu" ? t("Réactiver") : t("Suspendre"), f: "suspendre" },
            { text: t("Supprimer le compte"), f: "supprimer", danger: true },
          );
      }
      const choice = await chooseFromMenu(`${item.name} (${item.id})`, choices);
      if (!choice) return;
      if (choice.f === "rang") {
        const rank = await chooseRank(cloud, t`Rang de ${item.name}`, item.cloud_rank);
        if (
          rank &&
          rank.id !== item.cloud_rank &&
          (await act(
            url,
            { action: "rang", rank: rank.id },
            `${item.name} : ${rank.id ? rank.text.replace(" (actuel)", "") : t("aucun rang")}`,
          ))
        )
          reload();
      } else if (choice.f === "espace") {
        const value = await chooseQuota(t`Espace de ${item.name}`, item.quota, cloud, superAdmin);
        if (
          value !== undefined &&
          (await act(
            url,
            { action: "quota", go: value },
            t`Espace de ${item.name} : ${value == null ? t("illimité") : String(value).replace(".", ",") + " Go"}`,
          ))
        )
          reload();
      } else if (choice.f === "groupes") {
        const entry = await chooseFromMenu(
          t`Groupes de ${item.name}`,
          cloud.groups
            .filter((item) => item.id !== cloud.all)
            .map((entry) => ({ text: (item.groups.includes(entry.id) ? "✓ " : "") + entry.id, id: entry.id })),
        );
        if (!entry) return;
        const member = !item.groups.includes(entry.id);
        if (
          await act(
            url,
            { action: "groupe", group: entry.id, inside: member },
            member ? t`${item.name} ajouté à ${entry.id}` : t`${item.name} retiré de ${entry.id}`,
          )
        )
          reload();
      } else if (choice.f === "mdp") {
        if (
          !(await confirmAction({
            title: t`Nouveau mot de passe pour ${item.name} ?`,
            text: t("L'ancien ne marchera plus. Les applis Nextcloud de la personne devront se reconnecter."),
            confirmLabel: t("Changer"),
          }))
        )
          return;
        const result = await act(url, { action: "mdp" });
        if (result) await showCloudPassword(item, result.password);
      } else if (choice.f === "suspendre") {
        const active = item.state === "suspendu";
        if (
          !active &&
          !(await confirmAction({
            title: t`Suspendre ${item.name} ?`,
            text: t("La personne ne peut plus se connecter. Ses fichiers sont gardés."),
            confirmLabel: t("Suspendre"),
            danger: true,
          }))
        )
          return;
        if (await act(url, { action: "suspendre", active }, active ? t("Compte réactivé") : t("Compte suspendu")))
          reload();
      } else if (choice.f === "supprimer") {
        const value = await confirmAction({
          title: t`Supprimer ${item.name} ?`,
          text: t`Le compte et TOUS ses fichiers sont effacés. Pour confirmer, tape ${item.id}.`,
          field: "",
          confirmLabel: t("Supprimer"),
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
        title: t("Ajouter un compte"),
        text: t("Pour quelqu'un qui ne passe pas par l'inscription. Un mot de passe provisoire s'affichera."),
        fields: [
          { name: "name", label: t("Prénom et nom") },
          { name: "id", label: t("Identifiant (minuscules, sans espace)") },
          { name: "email", label: t("E-mail (facultatif)"), type: "email" },
          ...(cloud.ranks_enabled && cloud.ranks.length
            ? [
                {
                  name: "rank",
                  label: t("Rang"),
                  value: cloud.ranks[0].id,
                  options: [...[...cloud.ranks].reverse().map((item) => [item.id, item.name]), ["", t("Aucun rang")]],
                },
              ]
            : []),
        ],
        confirmLabel: t("Créer"),
      });
      if (!answers || answers === "secondary") return;
      const result = await act("/api/nuage/comptes", {
        id: answers.id.trim().toLowerCase(),
        name: answers.name,
        email: answers.email,
        rank: answers.rank || null,
      });
      if (result) {
        await showCloudPassword(
          { name: answers.name || answers.id, id: answers.id.trim().toLowerCase() },
          result.password,
        );
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
        { default_quota_gb: value },
        t`Nouveaux comptes : ${String(value).replace(".", ",")} Go`,
      ))
    )
      reload();
  };
  $("#nuage-plafond").onclick = async () => {
    const value = await confirmAction({
      title: t("Plafond par compte"),
      text: t("En Go. Un admin ne pourra pas donner plus à un compte."),
      field: String(cloud.cap_gb),
      confirmLabel: t("Changer"),
    });
    const item = value && parseFloat(value.replace(",", "."));
    if (item > 0 && (await act("/api/nuage/reglages", { cap_gb: item }, t`Plafond : ${value} Go`))) reload();
  };
}

async function showCloudPassword(item, password) {
  await new Promise((resolve) => {
    dialogForm.innerHTML = t`<h2>Mot de passe Nextcloud</h2>
      <p>Donne-le à <strong>${escapeHtml(item.name)}</strong> (identifiant <strong>${escapeHtml(item.id)}</strong>). Il ne sera plus affiché. Conseille-lui de le changer dans Nextcloud (Paramètres personnels, Sécurité).</p>
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
