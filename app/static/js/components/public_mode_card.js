// Carte « Serveur ouvert au public » de Réglages → Serveur, et la demande de fichiers par lien.
//
// Sommaire :
//   partagé : publicModeCard, requestFiles, publicModeOpen
//   interne : readinessHtml, inactiveHtml, saveSettings
import { api, formBody } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { state } from "../core/state.js";
import { confirmAction, toast } from "./dialogs.js";
import { t } from "../core/i18n.js";

// Ouvert au public ? Demandé au plus une fois par minute (l'écran Fichiers s'en sert pour son menu).
let publicCache = { value: false, time: 0 };
export async function publicModeOpen() {
  if (Date.now() - publicCache.time < 60000) return publicCache.value;
  try {
    publicCache = { value: (await api("/api/public/etat")).active, time: Date.now() };
  } catch (_) {
    publicCache = { value: false, time: Date.now() };
  }
  return publicCache.value;
}

function readinessHtml(items) {
  return `<ul class="liste-pret">${items
    .map(
      (item) =>
        `<li class="${item.ok ? "ok" : "a-faire"}"><span aria-hidden="true">${item.ok ? "✓" : "•"}</span> ${escapeHtml(item.text)}${item.ok ? "" : t(' <span class="note">(à faire)</span>')}</li>`,
    )
    .join("")}</ul>`;
}

function inactiveHtml(accounts, months) {
  if (!accounts.length) return t`<p class="note">Aucun compte sans connexion depuis plus de ${months} mois.</p>`;
  return t`<p class="note">${accounts.length} compte${accounts.length > 1 ? "s" : ""} sans connexion depuis plus de ${months} mois : préviens-les, puis supprime-les depuis <a href="#/nuage">Comptes Nextcloud</a>.</p>
    <ul class="liste-inactifs">${accounts
      .map(
        (account) =>
          `<li>${escapeHtml(account.name)} <span class="note">· ${account.inactive_since == null ? t("jamais connecté") : account.inactive_since + " jours"}</span></li>`,
      )
      .join("")}</ul>`;
}

async function saveSettings(settings) {
  const answers = await formBody({
    title: t("Réglages du mode public"),
    text: t("Ils servent aux mentions RGPD montrées à l'inscription, et aux liens publics de Nextcloud."),
    fields: [
      { name: "organisation", label: t("Nom de l'organisme (association, fablab…)"), value: settings.organisation },
      { name: "contact", label: t("Contact pour les données personnelles (e-mail)"), value: settings.contact },
      {
        name: "inactive_months",
        label: t("Compte inactif après (mois)"),
        value: String(settings.inactive_months),
        type: "number",
      },
      {
        name: "expiry_days",
        label: t("Durée de vie d'un lien public (jours)"),
        value: String(settings.expiry_days),
        type: "number",
      },
    ],
    confirmLabel: t("Enregistrer"),
  });
  if (!answers || answers === "secondary") return;
  try {
    await api("/api/public/reglages", {
      method: "POST",
      json: {
        ...answers,
        inactive_months: +answers.inactive_months || 12,
        expiry_days: +answers.expiry_days || 30,
      },
    });
    toast(t("Réglages enregistrés"));
  } catch (error) {
    toast(error.message, true);
  }
}

export async function publicModeCard() {
  const element = $("#public");
  if (!element) return;
  const overview = await api("/api/public");
  const settings = overview.settings;
  const isSuper = state.admin && state.admin.rank === "super";
  element.innerHTML = t`<div class="entete-carte"><h2>Serveur ouvert au public</h2>
      <span class="pastille ${settings.active ? "p-ok" : "p-gris"}">${settings.active ? t("Ouvert") : t("Privé")}</span></div>
    <p class="note" style="margin:6px 0 10px">Pour un fablab ou une association : des personnes que tu ne connais pas auront un compte. ${
      settings.active
        ? t(
            "Les liens publics de Nextcloud ont un mot de passe et une date limite imposés, les demandes de fichiers sont possibles, et Jabba te signale chaque mois les comptes inactifs.",
          )
        : t(
            "Cette option impose mot de passe et date limite aux liens publics de Nextcloud, ajoute les mentions RGPD à l'inscription et surveille les comptes inactifs. Elle n'ouvre rien sur Internet : brancher le domaine reste une étape à part.",
          )
    }</p>
    <h3 class="petit-titre">Avant d'ouvrir</h3>
    ${readinessHtml(overview.ready)}
    ${settings.active ? t`<h3 class="petit-titre">Comptes inactifs</h3>${inactiveHtml(overview.inactive, settings.inactive_months)}` : ""}
    ${
      isSuper
        ? t`<div class="actions" style="margin-top:12px">
      <button class="btn" type="button" id="public-reglages">Réglages</button>
      ${overview.nextcloud ? t('<button class="btn" type="button" id="public-mentions">Ajouter les mentions RGPD à l\'inscription</button>') : ""}
      <button class="btn${settings.active ? " btn-danger" : " btn-p"}" type="button" id="public-bascule">${settings.active ? t("Revenir en privé") : t("Ouvrir au public")}</button>
    </div>`
        : ""
    }`;
  const refresh = () => publicModeCard().catch((error) => toast(error.message, true));
  if (!isSuper) return;
  $("#public-reglages").onclick = () => saveSettings(settings).then(refresh);
  const notice = $("#public-mentions");
  if (notice)
    notice.onclick = async () => {
      const ok = await confirmAction({
        title: t("Ajouter les mentions RGPD ?"),
        text: overview.mentions,
        confirmLabel: t("Ajouter à la charte"),
      });
      if (!ok) return;
      try {
        await api("/api/public/mentions", { method: "POST" });
        toast(t("Mentions ajoutées à la charte d'inscription"));
      } catch (error) {
        toast(error.message, true);
      }
    };
  $("#public-bascule").onclick = async () => {
    try {
      publicCache.time = 0;
      await api("/api/public/ouvrir", { method: "POST", json: { active: !settings.active } });
      toast(settings.active ? t("Serveur revenu en privé") : t("Mode public activé"));
    } catch (error) {
      toast(error.message, true);
    }
    refresh();
  };
}

// Fichiers → ••• d'un dossier → « Demander des fichiers… » : un lien où l'on dépose sans rien voir.
export async function requestFiles(entry) {
  const answers = await formBody({
    title: t`Demander des fichiers dans « ${entry.name} »`,
    text: t(
      "Tu obtiens un lien à envoyer (SMS, e-mail). La personne y dépose ses fichiers sans compte, et ne voit rien de ce que contient déjà le dossier.",
    ),
    fields: [
      { name: "days", label: t("Valable pendant (jours, 90 au plus)"), value: "7", type: "number" },
      { name: "password", label: t("Mot de passe du lien (à envoyer séparément)"), value: "" },
    ],
    confirmLabel: t("Créer le lien"),
  });
  if (!answers || answers === "secondary") return;
  try {
    const result = await api("/api/demandes-fichiers", {
      method: "POST",
      json: { path: entry.path, days: +answers.days || 7, password: answers.password, title: entry.name },
    });
    try {
      await navigator.clipboard.writeText(result.url);
      toast(t`Lien copié, valable jusqu'au ${result.expire.split("-").reverse().join("/")}`);
    } catch (_) {
      await confirmAction({ title: t("Lien de dépôt"), text: result.url, confirmLabel: "OK" });
    }
  } catch (error) {
    toast(error.message, true);
  }
}
