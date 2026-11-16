// Écran Comptes : comptes Nextcloud (inscriptions, espace, rangs, groupes, dossiers communs).
//
// Sommaire :
//   partagé : pages.nuage (écran)
//   interne : chooseRank, chooseQuota, CLOUD_TABS, cloudTab, showCloudPassword
import { confirmAction, dialogForm, dialogElement, chooseFromMenu, toast } from "../components/dialogs.js";
import { api, formBody } from "../core/api.js";
import { folderTargets, groupLabel } from "../components/share.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatGigabytes, formatBytes } from "../core/format.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { loadingHtml } from "../core/tasks.js";

async function chooseRank(cloud, title, current) {
  const choices = [...cloud.rangs]
    .reverse()
    .map((rank) => ({ texte: rank.nom + (rank.id === current ? " (actuel)" : ""), id: rank.id }));
  choices.push({ texte: "Aucun rang" + (current ? "" : " (actuel)"), id: null });
  return chooseFromMenu(title, choices);
}

async function chooseQuota(title, current, cloud, superAdmin, unlimited = true) {
  const sizes = [1, 2, 5, 10, 20, 50, 100, 200].filter((value) => superAdmin || value <= cloud.plafond_go);
  const choices = sizes.map((value) => ({
    texte: `${value} Go${current === value * 1073741824 ? " (actuel)" : ""}`,
    go: value,
  }));
  choices.push({ texte: "Autre…", autre: true });
  if (superAdmin && unlimited) choices.push({ texte: "Illimité", go: null });
  const item = await chooseFromMenu(title + (superAdmin ? "" : ` (plafond : ${cloud.plafond_go} Go)`), choices);
  if (!item) return undefined;
  if (!item.autre) return item.go;
  const value = await confirmAction({ titre: title, texte: "En Go (par exemple 7,5).", champ: "" });
  const entry = value && parseFloat(value.replace(",", "."));
  return entry > 0 ? entry : undefined;
}

const CLOUD_TABS = [
  ["comptes", "Comptes"],
  ["partage", "Dossiers et rangs"],
  ["reglages", "Charte et réglages"],
];
let cloudTab = "comptes";
// Les dossiers partagés (avec « Arrêter le partage »), chargés à part : l'écran s'affiche sans les attendre.
async function loadSharedFolders(cloud) {
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
          `<div class="sauv-ligne"><span><strong>${escapeHtml(share.nom)}</strong><br><span class="note">${escapeHtml(share.chemin)} · ${escapeHtml(groupLabel(share.groupe, cloud))} : ${escapeHtml((shared.modes[share.mode] || "").toLowerCase())}</span></span><button class="btn btn-petit" data-arreter="${escapeHtml(share.id)}">Arrêter</button></div>`,
      )
      .join("") || '<p class="note">Aucun pour l\'instant.</p>';
  list.querySelectorAll("[data-arreter]").forEach((button) =>
    button.addEventListener("click", async () => {
      const share = shared.partages.find((item) => item.id === button.dataset.arreter);
      const confirmed = await confirmAction({
        titre: `Arrêter le partage de « ${share.nom} » ?`,
        texte: "Les personnes n'y auront plus accès. Les fichiers ne bougent pas.",
        valider: "Arrêter",
        danger: true,
      });
      if (!confirmed) return;
      try {
        await api(`/api/partages/${encodeURIComponent(share.id)}/arreter`, { method: "POST" });
        toast("Partage arrêté");
      } catch (error) {
        toast(error.message, true);
      }
      loadSharedFolders(cloud);
    }),
  );
}

pages.nuage = async () => {
  content.innerHTML = `<div class="entete"><div><h1>Comptes Nextcloud</h1></div></div>${loadingHtml(4)}`;
  const cloud = await api("/api/nuage");
  const superAdmin = cloud.rang === "super";
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
  if (!cloud.etat.present) {
    content.innerHTML = `<div class="entete"><div><h1>Comptes Nextcloud</h1></div></div>
      <section class="carte pad"><p>Nextcloud n'est pas allumé. Active-le dans l'<a href="#/outils">Établi</a>, puis reviens ici.</p></section>`;
    return;
  }
  if (!cloud.etat.pret) {
    content.innerHTML = `<div class="entete"><div><h1>Comptes Nextcloud</h1><p class="sous-titre">Pas encore en route.</p></div></div>
      <section class="carte pad"><h2>Mettre en route</h2>
        <p class="note" style="margin:8px 0">Comme Dropbox : chacun peut demander un compte, et un super admin le valide ici. Chaque compte a <strong>5 Go</strong> par défaut, que les admins peuvent changer sans dépasser le plafond. Des <strong>dossiers communs</strong> peuvent être partagés avec tous les membres ou avec un groupe.</p>
        <p class="note" style="margin:8px 0 14px">Jabba installe deux applis officielles de Nextcloud (inscription, dossiers communs) et pose les réglages. Les comptes déjà créés ne changent pas.</p>
        ${superAdmin ? '<div class="actions"><button class="btn btn-p" id="nuage-route">Mettre en route</button></div>' : '<p class="note">Seul un super admin peut le faire.</p>'}
      </section>`;
    const button = $("#nuage-route");
    if (button)
      button.onclick = async () => {
        button.disabled = true;
        button.textContent = "Installation… (1 à 2 minutes)";
        await act("/api/nuage/mettre-en-route", {}, "Comptes Nextcloud en route");
        reload();
      };
    return;
  }
  const pending = cloud.comptes.filter((item) => item.etat === "attente");
  const others = cloud.comptes.filter((item) => item.etat !== "attente");
  const accountRow = (item) => {
    const value = item.quota && item.utilise != null ? Math.min(100, Math.round((100 * item.utilise) / item.quota)) : 0;
    return `<button class="nuage-compte" data-compte="${escapeHtml(item.id)}">
      <span class="nuage-qui"><strong>${escapeHtml(item.nom)}</strong> <span class="note">${escapeHtml(item.id)}${item.email ? " · " + escapeHtml(item.email) : ""}</span>
        <span class="nuage-badges">${item.rang_nuage ? `<span class="pastille p-ok">${escapeHtml((cloud.rangs.find((rank) => rank.id === item.rang_nuage) || {}).nom || "")}</span>` : ""}${item.etat === "suspendu" ? '<span class="pastille p-rouge">Suspendu</span>' : ""}${item.admin ? '<span class="pastille p-gris">Admin Nextcloud</span>' : ""}${item.groupes
          .filter((item) => item !== cloud.tous && item !== "admin" && !item.startsWith("rang-"))
          .map((item) => `<span class="pastille p-gris">${escapeHtml(item)}</span>`)
          .join("")}</span></span>
      <span class="nuage-espace"><span>${item.utilise != null ? formatBytes(item.utilise) + " / " : ""}${formatGigabytes(item.quota)}</span>${item.quota ? `<span class="jauge"><span style="width:${value}%;${value >= 90 ? "background:var(--rouge)" : ""}"></span></span>` : ""}</span>
    </button>`;
  };
  const access = (item) =>
    item.acces
      .map(
        (currentAccess) =>
          `${escapeHtml(groupLabel(currentAccess.groupe, cloud))} : ${escapeHtml(cloud.modes[currentAccess.mode].nom.toLowerCase())}`,
      )
      .join(" · ") || "Personne";
  content.innerHTML = `
    <div class="entete"><div><h1>Comptes Nextcloud</h1><p class="sous-titre">${others.length} compte${others.length > 1 ? "s" : ""} · ${formatGigabytes(cloud.promis)} promis${cloud.espace ? ` · ${formatGigabytes(cloud.espace.total - cloud.espace.utilise)} libres sur le disque de données` : ""}</p></div>
      <div class="actions"><a class="btn" href="#/utilisateurs">Admins de Jabba</a>${superAdmin ? '<button class="btn btn-p" id="nuage-ajouter">Ajouter un compte</button>' : ""}</div></div>
    ${
      pending.length
        ? `<section class="carte pad nuage-attente"><h2>À valider</h2>
      ${pending
        .map(
          (
            item,
          ) => `<div class="sauv-ligne"><span><strong>${escapeHtml(item.nom)}</strong> <span class="note">${escapeHtml(item.id)}${item.email ? " · " + escapeHtml(item.email) : ""}</span></span>
        ${superAdmin ? `<span class="actions"><button class="btn btn-petit" data-refuser="${escapeHtml(item.id)}">Refuser</button><button class="btn btn-p btn-petit" data-valider="${escapeHtml(item.id)}">Valider</button></span>` : '<span class="note">Un super admin doit valider</span>'}</div>`,
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
      <div class="sauv-liste">${others.map(accountRow).join("") || '<p class="note">Aucun compte.</p>'}</div></section>
    </div>
    <div class="grille g2" data-onglet="partage">
      <section class="carte pad"><div class="entete-carte"><h2>Dossiers communs</h2><button class="btn btn-petit" id="nuage-dossier">Nouveau</button></div>
        <p class="note" style="margin:6px 0 8px">Ils apparaissent dans le Nextcloud de chaque personne qui y a accès.</p>
        <div class="sauv-liste">${cloud.dossiers.map((item) => `<button class="sauv-ligne" data-dossier="${item.id}"><span><strong>${escapeHtml(item.nom)}</strong><br><span class="note">${access(item)}</span></span><span class="note">${formatBytes(item.taille)}${item.quota ? " / " + formatGigabytes(item.quota) : ""}</span></button>`).join("") || '<p class="note">Aucun pour l\'instant.</p>'}</div></section>
      <section class="carte pad" id="partages"><div class="entete-carte"><h2>Dossiers partagés</h2></div>
        <p class="note" style="margin:6px 0 8px">Un dossier existant (même sur un disque), donné à voir à un rang ou un groupe. Pour en ajouter : Fichiers, menu du dossier, « Partager avec… ».</p>
        <div class="sauv-liste" id="partages-liste"><p class="note">Chargement…</p></div></section>
      <section class="carte pad"><div class="entete-carte"><h2>Rangs</h2>${cloud.createur ? `<button class="interrupteur" role="switch" aria-checked="${cloud.rangs_actif}" id="rangs-actif" aria-label="Utiliser des rangs"><span></span></button>` : ""}</div>
        <p class="note" style="margin:6px 0 8px">${
          cloud.rangs_actif
            ? "Du plus haut au plus bas. Un dossier commun donné à un rang est vu par ce rang et ceux au-dessus, pas par ceux en dessous."
            : "Désactivés. Avec les rangs (par exemple Adhérent, Employé, Gérant), on donne des dossiers communs à un rang et à ceux au-dessus."
        }${cloud.createur ? "" : " Seul le compte du serveur règle les rangs."}</p>
        ${
          cloud.rangs_actif
            ? `<div class="sauv-liste">${
                [...cloud.rangs]
                  .reverse()
                  .map(
                    (rank) =>
                      `<button class="sauv-ligne" data-rang="${escapeHtml(rank.id)}"${cloud.createur ? "" : " disabled"}><span><strong>${escapeHtml(rank.nom)}</strong></span><span class="note">${rank.membres} personne${rank.membres > 1 ? "s" : ""}</span></button>`,
                  )
                  .join("") || '<p class="note">Aucun rang pour l\'instant.</p>'
              }</div>
        ${cloud.createur ? '<div class="actions" style="margin-top:10px"><button class="btn btn-petit" id="rang-nouveau">Nouveau rang</button></div>' : ""}`
            : ""
        }
      </section>
      <section class="carte pad"><div class="entete-carte"><h2>Groupes</h2><button class="btn btn-petit" id="nuage-groupe">Nouveau</button></div>
        <p class="note" style="margin:6px 0 8px">Pour donner un dossier commun à quelques personnes (le fablab, la famille…). Chaque compte validé fait partie de « Tous les membres ».</p>
        <div class="sauv-liste">${cloud.groupes.map((item) => `<button class="sauv-ligne" data-groupe="${escapeHtml(item.id)}"><span><strong>${escapeHtml(groupLabel(item.id, cloud))}</strong></span><span class="note">${item.membres.length} membre${item.membres.length > 1 ? "s" : ""}</span></button>`).join("")}</div></section>
    </div>
    <div class="grille g2" data-onglet="reglages">
      ${
        superAdmin
          ? `<section class="carte pad"><h2>Charte d'inscription</h2>
        <p class="note" style="margin:6px 0 10px">Affichée sur le formulaire « S'inscrire » de Nextcloud : ce que la personne accepte en créant son compte.</p>
        <textarea id="nuage-charte" rows="9" maxlength="${cloud.charte_max}" style="width:100%">${escapeHtml(cloud.charte)}</textarea>
        <div class="actions" style="margin-top:10px"><button class="btn" id="charte-modele">Reprendre le modèle</button><button class="btn btn-p" id="charte-enregistrer">Enregistrer</button></div>
      </section>
      <section class="carte pad"><h2>Réglages</h2>
        <label class="ligne-reglage"><span><strong>Espace des nouveaux comptes</strong></span><button class="btn btn-petit" id="nuage-defaut">${escapeHtml(cloud.etat.quota_defaut.replace(" GB", " Go").replace(" MB", " Mo"))}</button></label>
        <label class="ligne-reglage"><span><strong>Plafond par compte</strong><br><span class="note">Un admin ne peut pas donner plus. Un super admin, si.</span></span><button class="btn btn-petit" id="nuage-plafond">${cloud.plafond_go} Go</button></label>
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
      if (await act("/api/nuage/charte", { texte: $("#nuage-charte").value }, "Charte enregistrée")) reload();
    };
    $("#charte-modele").onclick = () => {
      $("#nuage-charte").value = cloud.charte_defaut;
    };
  }

  // à valider
  content.querySelectorAll("[data-valider]").forEach((element) =>
    element.addEventListener("click", async () => {
      let rank = null;
      if (cloud.rangs_actif && cloud.rangs.length) {
        const chosen = await chooseRank(cloud, `Rang de ${element.dataset.valider}`, null);
        if (!chosen) return;
        rank = chosen.id;
      }
      if (
        await act(
          `/api/nuage/comptes/${encodeURIComponent(element.dataset.valider)}`,
          { action: "valider", rang: rank },
          "Compte validé",
        )
      )
        reload();
    }),
  );
  content.querySelectorAll("[data-refuser]").forEach((element) =>
    element.addEventListener("click", async () => {
      if (
        !(await confirmAction({
          titre: `Refuser ${element.dataset.refuser} ?`,
          texte: "La demande est supprimée. La personne pourra en refaire une.",
          valider: "Refuser",
          danger: true,
        }))
      )
        return;
      if (
        await act(
          `/api/nuage/comptes/${encodeURIComponent(element.dataset.refuser)}`,
          { action: "refuser" },
          "Demande refusée",
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
        { texte: `Changer l'espace (${formatGigabytes(item.quota)})`, f: "espace" },
        { texte: "Groupes", f: "groupes" },
      ];
      if (superAdmin && cloud.rangs_actif && cloud.rangs.length)
        choices.unshift({
          texte: `Rang (${(cloud.rangs.find((rank) => rank.id === item.rang_nuage) || { nom: "aucun" }).nom})`,
          f: "rang",
        });
      if (superAdmin) {
        choices.push({ texte: "Nouveau mot de passe", f: "mdp" });
        if (!item.admin)
          choices.push(
            { texte: item.etat === "suspendu" ? "Réactiver" : "Suspendre", f: "suspendre" },
            { texte: "Supprimer le compte", f: "supprimer", danger: true },
          );
      }
      const choice = await chooseFromMenu(`${item.nom} (${item.id})`, choices);
      if (!choice) return;
      if (choice.f === "rang") {
        const rank = await chooseRank(cloud, `Rang de ${item.nom}`, item.rang_nuage);
        if (
          rank &&
          rank.id !== item.rang_nuage &&
          (await act(
            url,
            { action: "rang", rang: rank.id },
            `${item.nom} : ${rank.id ? rank.texte.replace(" (actuel)", "") : "aucun rang"}`,
          ))
        )
          reload();
      } else if (choice.f === "espace") {
        const value = await chooseQuota(`Espace de ${item.nom}`, item.quota, cloud, superAdmin);
        if (
          value !== undefined &&
          (await act(
            url,
            { action: "quota", go: value },
            `Espace de ${item.nom} : ${value == null ? "illimité" : String(value).replace(".", ",") + " Go"}`,
          ))
        )
          reload();
      } else if (choice.f === "groupes") {
        const entry = await chooseFromMenu(
          `Groupes de ${item.nom}`,
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
            member ? `${item.nom} ajouté à ${entry.id}` : `${item.nom} retiré de ${entry.id}`,
          )
        )
          reload();
      } else if (choice.f === "mdp") {
        if (
          !(await confirmAction({
            titre: `Nouveau mot de passe pour ${item.nom} ?`,
            texte: "L'ancien ne marchera plus. Les applis Nextcloud de la personne devront se reconnecter.",
            valider: "Changer",
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
            titre: `Suspendre ${item.nom} ?`,
            texte: "La personne ne peut plus se connecter. Ses fichiers sont gardés.",
            valider: "Suspendre",
            danger: true,
          }))
        )
          return;
        if (await act(url, { action: "suspendre", actif: active }, active ? "Compte réactivé" : "Compte suspendu"))
          reload();
      } else if (choice.f === "supprimer") {
        const value = await confirmAction({
          titre: `Supprimer ${item.nom} ?`,
          texte: `Le compte et TOUS ses fichiers sont effacés. Pour confirmer, tape ${item.id}.`,
          champ: "",
          valider: "Supprimer",
          danger: true,
        });
        if (value !== item.id) {
          if (value !== null) toast("Pas confirmé : rien n'a été supprimé.", true);
          return;
        }
        if (await act(url, { action: "supprimer" }, "Compte supprimé")) reload();
      }
    }),
  );

  // ajouter
  const addButton = $("#nuage-ajouter");
  if (addButton)
    addButton.onclick = async () => {
      const answers = await formBody({
        titre: "Ajouter un compte",
        texte: "Pour quelqu'un qui ne passe pas par l'inscription. Un mot de passe provisoire s'affichera.",
        champs: [
          { nom: "nom", label: "Prénom et nom" },
          { nom: "id", label: "Identifiant (minuscules, sans espace)" },
          { nom: "email", label: "E-mail (facultatif)", type: "email" },
          ...(cloud.rangs_actif && cloud.rangs.length
            ? [
                {
                  nom: "rang",
                  label: "Rang",
                  valeur: cloud.rangs[0].id,
                  options: [...[...cloud.rangs].reverse().map((item) => [item.id, item.nom]), ["", "Aucun rang"]],
                },
              ]
            : []),
        ],
        valider: "Créer",
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

  // dossiers communs
  const chooseMode = (title) =>
    chooseFromMenu(
      title,
      Object.entries(cloud.modes).map(([key, item]) => ({ texte: `${item.nom} : ${item.detail}`, mode: key })),
    );
  $("#nuage-dossier").onclick = async () => {
    const name = await confirmAction({
      titre: "Nouveau dossier commun",
      texte: "Son nom, tel qu'il apparaîtra dans Nextcloud.",
      champ: "",
      valider: "Suivant",
    });
    if (!name) return;
    const item = await chooseFromMenu(
      "Pour qui ?",
      folderTargets(cloud).map((item) => ({ texte: groupLabel(item.id, cloud), id: item.id })),
    );
    if (!item) return;
    const entry = await chooseMode(`${groupLabel(item.id, cloud)} pourra…`);
    if (!entry) return;
    if (
      await act(
        "/api/nuage/dossiers",
        { action: "creer", nom: name, groupe: item.id, mode: entry.mode },
        `Dossier « ${name} » créé`,
      )
    )
      reload();
  };
  content.querySelectorAll("[data-dossier]").forEach((element) =>
    element.addEventListener("click", async () => {
      const item = cloud.dossiers.find((item) => item.id === +element.dataset.dossier);
      const choice = await chooseFromMenu(item.nom, [
        { texte: "Qui y a accès", f: "acces" },
        { texte: `Taille maximale (${item.quota ? formatGigabytes(item.quota) : "aucune"})`, f: "quota" },
        { texte: "Renommer", f: "renommer" },
        { texte: "Supprimer le dossier", f: "supprimer", danger: true },
      ]);
      if (!choice) return;
      const body = { id: item.id };
      if (choice.f === "acces") {
        const entry = await chooseFromMenu(
          `Accès à « ${item.nom} »`,
          folderTargets(cloud).map((entry) => {
            const currentAccess = item.acces.find((other) => other.groupe === entry.id);
            return {
              texte: `${groupLabel(entry.id, cloud)} : ${currentAccess ? cloud.modes[currentAccess.mode].nom.toLowerCase() : "pas d'accès"}`,
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
        if (entry.a) choices.push({ texte: "Pas d'accès", mode: null, danger: true });
        const choice = await chooseFromMenu(`${groupLabel(entry.id, cloud)} pourra…`, choices);
        if (!choice) return;
        Object.assign(body, { action: "acces", groupe: entry.id, mode: choice.mode });
      } else if (choice.f === "quota") {
        const value = await chooseQuota(`Taille maximale de « ${item.nom} »`, item.quota, { plafond_go: 100000 }, true);
        if (value === undefined) return;
        Object.assign(body, { action: "quota", go: value });
      } else if (choice.f === "renommer") {
        const name = await confirmAction({ titre: "Renommer", champ: item.nom, valider: "Renommer" });
        if (!name || name === item.nom) return;
        Object.assign(body, { action: "renommer", nom: name });
      } else {
        const value = await confirmAction({
          titre: `Supprimer « ${item.nom} » ?`,
          texte: "Le dossier et TOUT son contenu sont effacés pour tout le monde. Pour confirmer, tape SUPPRIMER.",
          champ: "",
          valider: "Supprimer",
          danger: true,
        });
        if (value !== "SUPPRIMER") {
          if (value !== null) toast("Pas confirmé : rien n'a été supprimé.", true);
          return;
        }
        body.action = "supprimer";
      }
      if (await act("/api/nuage/dossiers", body, "C'est fait")) reload();
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
          titre: "Désactiver les rangs ?",
          texte:
            "Chacun garde son rang, mais les dossiers donnés à un rang ne sont plus visibles jusqu'à la réactivation.",
          valider: "Désactiver",
          danger: true,
        }))
      )
        return;
      ranks({ action: "activer", actif: !cloud.rangs_actif }, cloud.rangs_actif ? "Rangs désactivés" : "Rangs activés");
    };
  const rankName = $("#rang-nouveau");
  if (rankName)
    rankName.onclick = async () => {
      const name = await confirmAction({
        titre: "Nouveau rang",
        texte: "Il se place tout en haut ; tu pourras le descendre ensuite.",
        champ: "",
        valider: "Créer",
      });
      if (name) ranks({ action: "creer", nom: name }, `Rang « ${name} » créé`);
    };
  content.querySelectorAll("[data-rang]").forEach((element) =>
    element.addEventListener("click", async () => {
      const rank = cloud.rangs.find((item) => item.id === element.dataset.rang),
        index = cloud.rangs.indexOf(rank);
      const choices = [{ texte: "Renommer", f: "renommer" }];
      if (index < cloud.rangs.length - 1) choices.push({ texte: "Monter d'un cran", f: "haut" });
      if (index > 0) choices.push({ texte: "Descendre d'un cran", f: "bas" });
      choices.push({ texte: "Supprimer le rang", f: "supprimer", danger: true });
      const choice = await chooseFromMenu(rank.nom, choices);
      if (!choice) return;
      if (choice.f === "renommer") {
        const name = await confirmAction({ titre: "Renommer le rang", champ: rank.nom, valider: "Renommer" });
        if (name && name !== rank.nom) ranks({ action: "renommer", id: rank.id, nom: name }, "Rang renommé");
      } else if (choice.f === "supprimer") {
        if (
          await confirmAction({
            titre: `Supprimer le rang ${rank.nom} ?`,
            texte: `Ses ${rank.membres} membre(s) descendent d'un rang. Les dossiers donnés à ce rang ne leur sont plus donnés.`,
            valider: "Supprimer",
            danger: true,
          })
        )
          ranks({ action: "supprimer", id: rank.id }, "Rang supprimé");
      } else ranks({ action: "deplacer", id: rank.id, sens: choice.f }, "Ordre changé");
    }),
  );

  // groupes
  $("#nuage-groupe").onclick = async () => {
    const name = await confirmAction({
      titre: "Nouveau groupe",
      texte: "Par exemple : Fablab, Famille…",
      champ: "",
      valider: "Créer",
    });
    if (name && (await act("/api/nuage/groupes", { nom: name }, `Groupe « ${name} » créé`))) reload();
  };
  content.querySelectorAll("[data-groupe]").forEach((element) =>
    element.addEventListener("click", async () => {
      const item = cloud.groupes.find((item) => item.id === element.dataset.groupe);
      const names = item.membres.map((id) => (cloud.comptes.find((item) => item.id === id) || { nom: id }).nom);
      if (item.id === cloud.tous)
        return confirmAction({
          titre: "Tous les membres",
          texte: names.join(", ") || "Personne pour l'instant.",
          valider: "OK",
        });
      const choice = await chooseFromMenu(item.id, [
        { texte: "Ajouter quelqu'un", f: "ajouter" },
        ...(names.length ? [{ texte: "Retirer quelqu'un", f: "retirer" }] : []),
        { texte: "Supprimer le groupe", f: "supprimer", danger: true },
      ]);
      if (!choice) return;
      if (choice.f === "supprimer") {
        if (
          !(await confirmAction({
            titre: `Supprimer le groupe ${item.id} ?`,
            texte:
              "Les comptes et les fichiers restent. Ses membres perdent l'accès aux dossiers communs donnés à ce groupe.",
            valider: "Supprimer",
            danger: true,
          }))
        )
          return;
        if (await act("/api/nuage/groupes", { nom: item.id, supprimer: true }, "Groupe supprimé")) reload();
        return;
      }
      const member = choice.f === "ajouter";
      const list = cloud.comptes.filter(
        (entry) => entry.etat !== "attente" && item.membres.includes(entry.id) !== member,
      );
      if (!list.length) return toast(member ? "Tout le monde en fait déjà partie." : "Personne à retirer.");
      const entry = await chooseFromMenu(
        member ? `Ajouter à ${item.id}` : `Retirer de ${item.id}`,
        list.map((item) => ({ texte: `${item.nom} (${item.id})`, id: item.id })),
      );
      if (
        entry &&
        (await act(
          `/api/nuage/comptes/${encodeURIComponent(entry.id)}`,
          { action: "groupe", groupe: item.id, dedans: member },
          "C'est fait",
        ))
      )
        reload();
    }),
  );

  // réglages
  if (!superAdmin) return;
  $("#nuage-defaut").onclick = async () => {
    const value = await chooseQuota("Espace des nouveaux comptes", null, cloud, false, false);
    if (
      value != null &&
      (await act(
        "/api/nuage/reglages",
        { quota_defaut_go: value },
        `Nouveaux comptes : ${String(value).replace(".", ",")} Go`,
      ))
    )
      reload();
  };
  $("#nuage-plafond").onclick = async () => {
    const value = await confirmAction({
      titre: "Plafond par compte",
      texte: "En Go. Un admin ne pourra pas donner plus à un compte.",
      champ: String(cloud.plafond_go),
      valider: "Changer",
    });
    const item = value && parseFloat(value.replace(",", "."));
    if (item > 0 && (await act("/api/nuage/reglages", { plafond_go: item }, `Plafond : ${value} Go`))) reload();
  };
};

async function showCloudPassword(item, password) {
  await new Promise((resolve) => {
    dialogForm.innerHTML = `<h2>Mot de passe Nextcloud</h2>
      <p>Donne-le à <strong>${escapeHtml(item.nom)}</strong> (identifiant <strong>${escapeHtml(item.id)}</strong>). Il ne sera plus affiché. Conseille-lui de le changer dans Nextcloud (Paramètres personnels, Sécurité).</p>
      <p class="code-secours">${escapeHtml(password)}</p>
      <div class="actions"><button class="btn" type="button" id="copier-mdp">Copier</button><button class="btn btn-p" value="ok" type="submit">C'est noté</button></div>`;
    dialogElement.onclose = () => resolve();
    dialogElement.showModal();
    $("#copier-mdp").onclick = async () => {
      try {
        await navigator.clipboard.writeText(password);
        toast("Copié");
      } catch (_) {
        toast("Copie impossible : sélectionne-le à la main.", true);
      }
    };
  });
}
