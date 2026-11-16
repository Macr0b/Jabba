// Carte « Serveur ouvert au public » de l'écran Système, et la demande de fichiers par lien.
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
    publicCache = { value: (await api("/api/public/etat")).actif, time: Date.now() };
  } catch (_) {
    publicCache = { value: false, time: Date.now() };
  }
  return publicCache.value;
}

function readinessHtml(items) {
  return `<ul class="liste-pret">${items
    .map(
      (item) =>
        `<li class="${item.ok ? "ok" : "a-faire"}"><span aria-hidden="true">${item.ok ? "✓" : "•"}</span> ${escapeHtml(item.texte)}${item.ok ? "" : t(' <span class="note">(à faire)</span>')}</li>`,
    )
    .join("")}</ul>`;
}

function inactiveHtml(accounts, months) {
  if (!accounts.length) return t`<p class="note">Aucun compte sans connexion depuis plus de ${months} mois.</p>`;
  return t`<p class="note">${accounts.length} compte${accounts.length > 1 ? "s" : ""} sans connexion depuis plus de ${months} mois : préviens-les, puis supprime-les depuis <a href="#/nuage">Comptes Nextcloud</a>.</p>
    <ul class="liste-inactifs">${accounts
      .map(
        (account) =>
          `<li>${escapeHtml(account.nom)} <span class="note">· ${account.inactif_depuis == null ? t("jamais connecté") : account.inactif_depuis + " jours"}</span></li>`,
      )
      .join("")}</ul>`;
}

async function saveSettings(settings) {
  const answers = await formBody({
    titre: t("Réglages du mode public"),
    texte: t("Ils servent aux mentions RGPD montrées à l'inscription, et aux liens publics de Nextcloud."),
    champs: [
      { nom: "organisme", label: t("Nom de l'organisme (association, fablab…)"), valeur: settings.organisme },
      { nom: "contact", label: t("Contact pour les données personnelles (e-mail)"), valeur: settings.contact },
      {
        nom: "inactif_mois",
        label: t("Compte inactif après (mois)"),
        valeur: String(settings.inactif_mois),
        type: "number",
      },
      {
        nom: "expiration_jours",
        label: t("Durée de vie d'un lien public (jours)"),
        valeur: String(settings.expiration_jours),
        type: "number",
      },
    ],
    valider: t("Enregistrer"),
  });
  if (!answers || answers === "secondaire") return;
  try {
    await api("/api/public/reglages", {
      method: "POST",
      json: {
        ...answers,
        inactif_mois: +answers.inactif_mois || 12,
        expiration_jours: +answers.expiration_jours || 30,
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
  const settings = overview.reglages;
  const isSuper = state.admin && state.admin.rang === "super";
  element.innerHTML = t`<div class="entete-carte"><h2>Serveur ouvert au public</h2>
      <span class="pastille ${settings.actif ? "p-ok" : "p-gris"}">${settings.actif ? t("Ouvert") : t("Privé")}</span></div>
    <p class="note" style="margin:6px 0 10px">Pour un fablab ou une association : des personnes que tu ne connais pas auront un compte. ${
      settings.actif
        ? t(
            "Les liens publics de Nextcloud ont un mot de passe et une date limite imposés, les demandes de fichiers sont possibles, et Jabba te signale chaque mois les comptes inactifs.",
          )
        : t(
            "Cette option impose mot de passe et date limite aux liens publics de Nextcloud, ajoute les mentions RGPD à l'inscription et surveille les comptes inactifs. Elle n'ouvre rien sur Internet : brancher le domaine reste une étape à part.",
          )
    }</p>
    <h3 class="petit-titre">Avant d'ouvrir</h3>
    ${readinessHtml(overview.pret)}
    ${settings.actif ? t`<h3 class="petit-titre">Comptes inactifs</h3>${inactiveHtml(overview.inactifs, settings.inactif_mois)}` : ""}
    ${
      isSuper
        ? t`<div class="actions" style="margin-top:12px">
      <button class="btn" type="button" id="public-reglages">Réglages</button>
      ${overview.nextcloud ? t('<button class="btn" type="button" id="public-mentions">Ajouter les mentions RGPD à l\'inscription</button>') : ""}
      <button class="btn${settings.actif ? " btn-danger" : " btn-p"}" type="button" id="public-bascule">${settings.actif ? t("Revenir en privé") : t("Ouvrir au public")}</button>
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
        titre: t("Ajouter les mentions RGPD ?"),
        texte: overview.mentions,
        valider: t("Ajouter à la charte"),
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
      await api("/api/public/ouvrir", { method: "POST", json: { actif: !settings.actif } });
      toast(settings.actif ? t("Serveur revenu en privé") : t("Mode public activé"));
    } catch (error) {
      toast(error.message, true);
    }
    refresh();
  };
}

// Fichiers → ••• d'un dossier → « Demander des fichiers… » : un lien où l'on dépose sans rien voir.
export async function requestFiles(entry) {
  const answers = await formBody({
    titre: t`Demander des fichiers dans « ${entry.nom} »`,
    texte: t(
      "Tu obtiens un lien à envoyer (SMS, e-mail). La personne y dépose ses fichiers sans compte, et ne voit rien de ce que contient déjà le dossier.",
    ),
    champs: [
      { nom: "jours", label: t("Valable pendant (jours, 90 au plus)"), valeur: "7", type: "number" },
      { nom: "mot_de_passe", label: t("Mot de passe du lien (à envoyer séparément)"), valeur: "" },
    ],
    valider: t("Créer le lien"),
  });
  if (!answers || answers === "secondaire") return;
  try {
    const result = await api("/api/demandes-fichiers", {
      method: "POST",
      json: { chemin: entry.chemin, jours: +answers.jours || 7, mot_de_passe: answers.mot_de_passe, titre: entry.nom },
    });
    try {
      await navigator.clipboard.writeText(result.url);
      toast(t`Lien copié, valable jusqu'au ${result.expire.split("-").reverse().join("/")}`);
    } catch (_) {
      await confirmAction({ titre: t("Lien de dépôt"), texte: result.url, valider: "OK" });
    }
  } catch (error) {
    toast(error.message, true);
  }
}
