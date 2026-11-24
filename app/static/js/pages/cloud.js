// Écran Comptes : comptes Nextcloud (inscriptions, espace, rangs, groupes, dossiers communs).
//
// Sommaire :
//   partagé : pages.nuage (écran)
//   interne : CLOUD_TABS, cloudTab
//   (comptes : components/cloud_accounts.js ; dossiers, rangs, groupes : components/cloud_sharing.js)
import { toast } from "../components/dialogs.js";
import { api } from "../core/api.js";
import { groupLabel } from "../components/share.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatGigabytes, formatBytes } from "../core/format.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { loadingHtml } from "../core/tasks.js";
import { t } from "../core/i18n.js";
import { bindAccounts, bindCloudSettings } from "../components/cloud_accounts.js";
import { bindSharing } from "../components/cloud_sharing.js";

const CLOUD_TABS = [
  ["comptes", t("Comptes")],
  ["partage", t("Dossiers et rangs")],
  ["reglages", t("Charte et réglages")],
];
let cloudTab = "comptes";

pages.nuage = async () => {
  content.innerHTML = t`<div class="entete"><div><h1>Comptes Nextcloud</h1></div></div>${loadingHtml(4)}`;
  const cloud = await api("/api/nuage");
  const superAdmin = cloud.rank === "super";
  const reload = () => pages.nuage().catch(showError);
  const act = async (url, json, ok) => {
    try {
      const result = await api(url, { method: "POST", json });
      if (ok) toast(ok);
      return result;
    } catch (error) {
      toast(error.message, true);
      return null;
    }
  };
  if (!cloud.state.present) {
    content.innerHTML = t`<div class="entete"><div><h1>Comptes Nextcloud</h1></div></div>
      <section class="carte pad"><p>Nextcloud n'est pas allumé. Active-le dans l'<a href="#/outils">Établi</a>, puis reviens ici.</p></section>`;
    return;
  }
  if (!cloud.state.ready) {
    content.innerHTML = t`<div class="entete"><div><h1>Comptes Nextcloud</h1><p class="sous-titre">Pas encore en route.</p></div></div>
      <section class="carte pad"><h2>Mettre en route</h2>
        <p class="note" style="margin:8px 0">Comme Dropbox : chacun peut demander un compte, et un super admin le valide ici. Chaque compte a <strong>5 Go</strong> par défaut, que les admins peuvent changer sans dépasser le plafond. Des <strong>dossiers communs</strong> peuvent être partagés avec tous les membres ou avec un groupe.</p>
        <p class="note" style="margin:8px 0 14px">Jabba installe deux applis officielles de Nextcloud (inscription, dossiers communs) et pose les réglages. Les comptes déjà créés ne changent pas.</p>
        ${superAdmin ? t('<div class="actions"><button class="btn btn-p" id="nuage-route">Mettre en route</button></div>') : t('<p class="note">Seul un super admin peut le faire.</p>')}
      </section>`;
    const button = $("#nuage-route");
    if (button)
      button.onclick = async () => {
        button.disabled = true;
        button.textContent = t("Installation… (1 à 2 minutes)");
        await act("/api/nuage/mettre-en-route", {}, t("Comptes Nextcloud en route"));
        reload();
      };
    return;
  }
  const pending = cloud.accounts.filter((item) => item.state === "attente");
  const others = cloud.accounts.filter((item) => item.state !== "attente");
  const accountRow = (item) => {
    const value = item.quota && item.used != null ? Math.min(100, Math.round((100 * item.used) / item.quota)) : 0;
    return `<button class="nuage-compte" data-compte="${escapeHtml(item.id)}">
      <span class="nuage-qui"><strong>${escapeHtml(item.name)}</strong> <span class="note">${escapeHtml(item.id)}${item.email ? " · " + escapeHtml(item.email) : ""}</span>
        <span class="nuage-badges">${item.cloud_rank ? `<span class="pastille p-ok">${escapeHtml((cloud.ranks.find((rank) => rank.id === item.cloud_rank) || {}).name || "")}</span>` : ""}${item.state === "suspendu" ? t('<span class="pastille p-rouge">Suspendu</span>') : ""}${item.admin ? t('<span class="pastille p-gris">Admin Nextcloud</span>') : ""}${item.groups
          .filter((item) => item !== cloud.all && item !== "admin" && !item.startsWith("rang-"))
          .map((item) => `<span class="pastille p-gris">${escapeHtml(item)}</span>`)
          .join("")}</span></span>
      <span class="nuage-espace"><span>${item.used != null ? formatBytes(item.used) + " / " : ""}${formatGigabytes(item.quota)}</span>${item.quota ? `<span class="jauge"><span style="width:${value}%;${value >= 90 ? "background:var(--rouge)" : ""}"></span></span>` : ""}</span>
    </button>`;
  };
  const access = (item) =>
    item.access
      .map(
        (currentAccess) =>
          `${escapeHtml(groupLabel(currentAccess.group, cloud))} : ${escapeHtml(cloud.modes[currentAccess.mode].name.toLowerCase())}`,
      )
      .join(" · ") || t("Personne");
  content.innerHTML = t`
    <div class="entete"><div><h1>Comptes Nextcloud</h1><p class="sous-titre">${others.length} compte${others.length > 1 ? "s" : ""} · ${formatGigabytes(cloud.promised)} promis${cloud.space ? t` · ${formatGigabytes(cloud.space.total - cloud.space.used)} libres sur le disque de données` : ""}</p></div>
      <div class="actions"><a class="btn" href="#/utilisateurs">Admins de Jabba</a>${superAdmin ? t('<button class="btn btn-p" id="nuage-ajouter">Ajouter un compte</button>') : ""}</div></div>
    ${
      pending.length
        ? t`<section class="carte pad nuage-attente"><h2>À valider</h2>
      ${pending
        .map(
          (
            item,
          ) => `<div class="sauv-ligne"><span><strong>${escapeHtml(item.name)}</strong> <span class="note">${escapeHtml(item.id)}${item.email ? " · " + escapeHtml(item.email) : ""}</span></span>
        ${superAdmin ? t`<span class="actions"><button class="btn btn-petit" data-refuser="${escapeHtml(item.id)}">Refuser</button><button class="btn btn-p btn-petit" data-valider="${escapeHtml(item.id)}">Valider</button></span>` : t('<span class="note">Un super admin doit valider</span>')}</div>`,
        )
        .join("")}
    </section>`
        : ""
    }
    <nav class="sous-onglets" role="tablist">${CLOUD_TABS.filter(([key]) => key !== "reglages" || superAdmin)
      .map(
        ([key, name]) =>
          `<button type="button" role="tab" data-onglet-btn="${key}" aria-selected="${key === cloudTab}">${name}${key === "comptes" && pending.length ? ` <span class="compteur">${pending.length}</span>` : ""}</button>`,
      )
      .join("")}</nav>
    <div data-onglet="comptes">
    <section class="carte pad"><h2>Comptes</h2>
      <div class="sauv-liste">${others.map(accountRow).join("") || t('<p class="note">Aucun compte.</p>')}</div></section>
    </div>
    <div class="grille g2" data-onglet="partage">
      <section class="carte pad"><div class="entete-carte"><h2>Dossiers communs</h2><button class="btn btn-petit" id="nuage-dossier">Nouveau</button></div>
        <p class="note" style="margin:6px 0 8px">Ils apparaissent dans le Nextcloud de chaque personne qui y a accès.</p>
        <div class="sauv-liste">${cloud.folders.map((item) => `<button class="sauv-ligne" data-dossier="${item.id}"><span><strong>${escapeHtml(item.name)}</strong><br><span class="note">${access(item)}</span></span><span class="note">${formatBytes(item.size)}${item.quota ? " / " + formatGigabytes(item.quota) : ""}</span></button>`).join("") || t('<p class="note">Aucun pour l\'instant.</p>')}</div></section>
      <section class="carte pad" id="partages"><div class="entete-carte"><h2>Dossiers partagés</h2></div>
        <p class="note" style="margin:6px 0 8px">Un dossier existant (même sur un disque), donné à voir à un rang ou un groupe. Pour en ajouter : Fichiers, menu du dossier, « Partager avec… ».</p>
        <div class="sauv-liste" id="partages-liste"><p class="note">Chargement…</p></div></section>
      <section class="carte pad"><div class="entete-carte"><h2>Rangs</h2>${cloud.creator ? t`<button class="interrupteur" role="switch" aria-checked="${cloud.ranks_enabled}" id="rangs-actif" aria-label="Utiliser des rangs"><span></span></button>` : ""}</div>
        <p class="note" style="margin:6px 0 8px">${
          cloud.ranks_enabled
            ? t(
                "Du plus haut au plus bas. Un dossier commun donné à un rang est vu par ce rang et ceux au-dessus, pas par ceux en dessous.",
              )
            : t(
                "Désactivés. Avec les rangs (par exemple Adhérent, Employé, Gérant), on donne des dossiers communs à un rang et à ceux au-dessus.",
              )
        }${cloud.creator ? "" : t(" Seul le compte du serveur règle les rangs.")}</p>
        ${
          cloud.ranks_enabled
            ? `<div class="sauv-liste">${
                [...cloud.ranks]
                  .reverse()
                  .map(
                    (rank) =>
                      `<button class="sauv-ligne" data-rang="${escapeHtml(rank.id)}"${cloud.creator ? "" : " disabled"}><span><strong>${escapeHtml(rank.name)}</strong></span><span class="note">${rank.members} personne${rank.members > 1 ? "s" : ""}</span></button>`,
                  )
                  .join("") || t('<p class="note">Aucun rang pour l\'instant.</p>')
              }</div>
        ${cloud.creator ? t('<div class="actions" style="margin-top:10px"><button class="btn btn-petit" id="rang-nouveau">Nouveau rang</button></div>') : ""}`
            : ""
        }
      </section>
      <section class="carte pad"><div class="entete-carte"><h2>Groupes</h2><button class="btn btn-petit" id="nuage-groupe">Nouveau</button></div>
        <p class="note" style="margin:6px 0 8px">Pour donner un dossier commun à quelques personnes (le fablab, la famille…). Chaque compte validé fait partie de « Tous les membres ».</p>
        <div class="sauv-liste">${cloud.groups.map((item) => `<button class="sauv-ligne" data-groupe="${escapeHtml(item.id)}"><span><strong>${escapeHtml(groupLabel(item.id, cloud))}</strong></span><span class="note">${item.members.length} membre${item.members.length > 1 ? "s" : ""}</span></button>`).join("")}</div></section>
    </div>
    <div class="grille g2" data-onglet="reglages">
      ${
        superAdmin
          ? t`<section class="carte pad"><h2>Charte d'inscription</h2>
        <p class="note" style="margin:6px 0 10px">Affichée sur le formulaire « S'inscrire » de Nextcloud : ce que la personne accepte en créant son compte.</p>
        <textarea id="nuage-charte" rows="9" maxlength="${cloud.charter_max}" style="width:100%">${escapeHtml(cloud.charter)}</textarea>
        <div class="actions" style="margin-top:10px"><button class="btn" id="charte-modele">Reprendre le modèle</button><button class="btn btn-p" id="charte-enregistrer">Enregistrer</button></div>
      </section>
      <section class="carte pad"><h2>Réglages</h2>
        <label class="ligne-reglage"><span><strong>Espace des nouveaux comptes</strong></span><button class="btn btn-petit" id="nuage-defaut">${escapeHtml(cloud.state.default_quota.replace(" GB", " Go").replace(" MB", " Mo"))}</button></label>
        <label class="ligne-reglage"><span><strong>Plafond par compte</strong><br><span class="note">Un admin ne peut pas donner plus. Un super admin, si.</span></span><button class="btn btn-petit" id="nuage-plafond">${cloud.cap_gb} Go</button></label>
        <p class="note" style="margin-top:10px">Inscription : lien « S'inscrire » sur la page de connexion de Nextcloud. Tant que Nextcloud n'a pas d'adresse publique, seuls ceux qui ont accès au serveur peuvent l'ouvrir. L'adresse e-mail n'est pas vérifiée (pas encore de serveur d'e-mails).</p>
      </section>`
          : ""
      }
    </div>`;
  // Onglets : un seul visible à la fois, et on revient sur le même après une action.
  const showTab = (key) => {
    cloudTab = key;
    content.querySelectorAll("[data-onglet]").forEach((element) => {
      element.hidden = element.dataset.onglet !== key;
    });
    content
      .querySelectorAll("[data-onglet-btn]")
      .forEach((element) => element.setAttribute("aria-selected", String(element.dataset.ongletBtn === key)));
  };
  content
    .querySelectorAll("[data-onglet-btn]")
    .forEach((element) => element.addEventListener("click", () => showTab(element.dataset.ongletBtn)));
  showTab(content.querySelector(`[data-onglet-btn="${cloudTab}"]`) ? cloudTab : "comptes");
  const choice = $("#charte-enregistrer");
  if (choice) {
    choice.onclick = async () => {
      if (await act("/api/nuage/charte", { text: $("#nuage-charte").value }, t("Charte enregistrée"))) reload();
    };
    $("#charte-modele").onclick = () => {
      $("#nuage-charte").value = cloud.default_charter;
    };
  }

  const context = { cloud, superAdmin, reload, act };
  bindAccounts(context);
  bindSharing(context);
  if (superAdmin) bindCloudSettings(context);
};
