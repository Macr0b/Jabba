// Écran Admins de Jabba : comptes admin, appareils connectés, rangs.
//
// Sommaire :
//   partagé : pages.utilisateurs (écran)
//   interne : ROLE_NAMES, showTemporaryPassword
import { confirmAction, dialogForm, dialogElement, chooseFromMenu, toast } from "../components/dialogs.js";
import { api, formBody } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { timeAgo } from "../core/format.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { loadingHtml } from "../core/tasks.js";

// ------------------------------------------------------------ utilisateurs
const ROLE_NAMES = { super: "Super admin", admin: "Admin" };

pages.utilisateurs = async () => {
  content.innerHTML = `<div class="entete"><div><h1>Admins de Jabba</h1></div></div>${loadingHtml(4)}`;
  const data = await api("/api/comptes");
  const superAdmin = data.moi.rang === "super";
  const byLogin = Object.fromEntries(
    data.comptes.filter((item) => item.tailscale).map((item) => [item.tailscale, item]),
  );
  content.innerHTML = `
    <div class="entete"><div><h1>Admins de Jabba</h1><p class="sous-titre">Les admins de l'appli, et qui se connecte. Tu es <strong>${escapeHtml(data.moi.identifiant)}</strong> · ${ROLE_NAMES[data.moi.rang]}.</p></div>
      <div class="actions"><a class="btn" href="#/nuage">Comptes Nextcloud</a><button class="btn btn-p" id="ajouter">Ajouter un admin</button></div></div>
    ${data.demandes
      .map(
        (
          item,
        ) => `<div class="bandeau demande"><span><strong>${escapeHtml(item.nom)}</strong> (${escapeHtml(item.identifiant)}) a oublié son mot de passe · demandé par ${escapeHtml(item.demandeur)}, ${escapeHtml(timeAgo(item.t))}.${item.autre_personne ? " <strong>Attention : demandé depuis le compte Tailscale de quelqu'un d'autre.</strong> Le mot de passe provisoire n'ira qu'à son compte à lui." : ""}</span>
      <span class="actions"><button class="btn btn-petit btn-p" data-accepter="${escapeHtml(item.id)}">Accepter</button><button class="btn btn-petit" data-refuser="${escapeHtml(item.id)}">Refuser</button></span></div>`,
      )
      .join("")}
    ${!superAdmin && data.demandes_n ? `<div class="bandeau"><span>${data.demandes_n} demande${data.demandes_n > 1 ? "s" : ""} de mot de passe en attente : seul un super admin peut y répondre.</span></div>` : ""}
    <section class="carte tableau" aria-label="Comptes admin">
      <div class="ligne titres"><span>Compte</span><span class="col-l">Rang</span><span class="col-l">Dernière connexion</span><span></span></div>
      ${data.comptes
        .map(
          (item, index) => `
        <div class="ligne">
          <span class="nom"><span class="avatar" aria-hidden="true">${escapeHtml((item.nom || item.identifiant)[0].toUpperCase())}</span>
            <span>${escapeHtml(item.nom)}<span class="detail-chemin">${escapeHtml(item.identifiant)}${item.systeme ? " · mot de passe du serveur" : item.tailscale ? " · Tailscale : " + escapeHtml(item.tailscale) : " · sans compte Tailscale"}${item.provisoire ? " · mot de passe provisoire" : ""}</span>
            <span class="mobile-detail">${ROLE_NAMES[item.rang]}</span></span></span>
          <span class="col-l"><span class="pastille ${item.rang === "super" ? "p-alerte" : "p-gris"}">${ROLE_NAMES[item.rang]}</span></span>
          <span class="gris col-l">${item.systeme ? "—" : escapeHtml(timeAgo(item.derniere_connexion))}</span>
          ${item.systeme ? "<span></span>" : `<button class="btn-menu" data-i="${index}" aria-label="Actions pour ${escapeHtml(item.nom)}">•••</button>`}
        </div>`,
        )
        .join("")}
    </section>
    <section class="carte pad">
      <label class="ligne-reglage"><span><strong>Espace ouvert à tous les admins</strong><br><span class="note">${data.reglages.espace_admins ? "Tous les admins peuvent ajouter des comptes. Seul un super admin peut nommer ou retirer un super admin." : "Seul un super admin peut ouvrir cet espace."}${superAdmin ? "" : " Réglé par le super admin."}</span></span>
        <button class="interrupteur" role="switch" aria-checked="${data.reglages.espace_admins}" id="espace"${superAdmin ? "" : " disabled"}><span></span></button></label>
    </section>
    <section class="carte pad">
      <h2>Qui se connecte</h2>
      <p class="note" style="margin:6px 0 10px">Rempli tout seul par Tailscale : chaque personne qui ouvre l'appli, depuis quel appareil.</p>
      <div class="journal">${
        data.visites
          .map(
            (item) => `<div class="evenement">
          <span class="famille">${data.proprietaires.includes(item.login) ? "Propriétaire" : byLogin[item.login] ? ROLE_NAMES[byLogin[item.login].rang] : "Accès"}</span>
          <span class="texte-ev">${escapeHtml(item.nom)} <span class="note">· ${escapeHtml(item.login)}${item.appareil ? " · " + escapeHtml(item.appareil) : ""} · ${item.n || 1} visite${(item.n || 1) > 1 ? "s" : ""}</span></span>
          <span class="note quand-ev">${escapeHtml(timeAgo(item.derniere))}</span></div>`,
          )
          .join("") || '<p class="note">Personne pour l\'instant.</p>'
      }</div>
      ${
        data.refus.length
          ? `<h2 style="margin-top:16px">Accès refusés</h2><div class="journal">${data.refus
              .map(
                (item) => `<div class="evenement f-admin"><span class="famille">Refusé</span>
          <span class="texte-ev">${escapeHtml(item.login)}${item.appareil ? ` <span class="note">· ${escapeHtml(item.appareil)}</span>` : ""}</span><span class="note quand-ev">${escapeHtml(timeAgo(item.t))}</span></div>`,
              )
              .join("")}</div>`
          : ""
      }
      <p class="note" style="margin-top:10px">Pour donner l'accès à quelqu'un : ajoute-le comme admin avec son compte Tailscale. Les propriétaires sont ceux du fichier de réglages du serveur.</p>
    </section>
    <section class="carte pad"><h2>Appareils connectés</h2>
      <p class="note" style="margin:6px 0 10px">Chaque appareil reste connecté 30 jours. Déconnecte celui que tu as perdu ou prêté.${superAdmin ? "" : " Tu vois tes propres appareils."}</p>
      <div class="journal" id="appareils"><div class="squelette"></div></div></section>`;
  api("/api/sessions")
    .then((seconds) => {
      const element = $("#appareils");
      if (!element) return;
      element.innerHTML =
        seconds
          .map(
            (item) => `<div class="evenement"><span class="famille">${escapeHtml(item.identifiant)}</span>
      <span class="texte-ev">${escapeHtml(item.appareil || "Appareil inconnu")} <span class="note">· connecté ${escapeHtml(timeAgo(item.cree))} · vu ${escapeHtml(timeAgo(item.vu))}</span></span>
      <span>${item.actuelle ? '<span class="pastille p-ok">Cet appareil</span>' : `<button class="btn btn-petit" data-session="${escapeHtml(item.id)}">Déconnecter</button>`}</span></div>`,
          )
          .join("") || '<p class="note">Aucun.</p>';
      element.querySelectorAll("[data-session]").forEach((element) =>
        element.addEventListener("click", async () => {
          try {
            await api(`/api/sessions/${encodeURIComponent(element.dataset.session)}/fermer`, { method: "POST" });
            toast("Appareil déconnecté");
          } catch (error) {
            toast(error.message, true);
          }
          pages.utilisateurs().catch(showError);
        }),
      );
    })
    .catch(() => {});

  const reload = () => pages.utilisateurs().catch(showError);
  content.querySelectorAll("[data-accepter],[data-refuser]").forEach((element) =>
    element.addEventListener("click", async () => {
      const accept = !!element.dataset.accepter;
      try {
        await api(`/api/comptes-demandes/${encodeURIComponent(element.dataset.accepter || element.dataset.refuser)}`, {
          method: "POST",
          json: { accepter: accept },
        });
        toast(accept ? "Acceptée : le mot de passe provisoire s'affichera sur son appareil" : "Demande refusée");
      } catch (error) {
        toast(error.message, true);
      }
      reload();
    }),
  );
  const space = $("#espace");
  if (space && superAdmin)
    space.onclick = async () => {
      const opened = space.getAttribute("aria-checked") !== "true";
      try {
        await api("/api/comptes-reglages", { method: "POST", json: { espace_admins: opened } });
      } catch (error) {
        toast(error.message, true);
      }
      reload();
    };
  $("#ajouter").onclick = async () => {
    const answers = await formBody({
      titre: "Ajouter un admin",
      texte:
        "Un mot de passe provisoire est créé. Avec son compte Tailscale, il s'affiche sur l'appli de la personne ; sinon, ici, pour que tu le lui donnes.",
      champs: [
        { nom: "nom", label: "Nom affiché", indice: "Camille" },
        { nom: "identifiant", label: "Identifiant de connexion", indice: "camille" },
        {
          nom: "tailscale",
          label: "Compte Tailscale (facultatif)",
          indice: "adresse avec laquelle elle se connecte à Tailscale",
        },
        ...(superAdmin
          ? [
              {
                nom: "rang",
                label: "Rang",
                options: [
                  ["admin", "Admin"],
                  ["super", "Super admin"],
                ],
                valeur: "admin",
              },
            ]
          : []),
      ],
      valider: "Ajouter",
    });
    if (!answers || answers === "secondaire") return;
    try {
      const data = await api("/api/comptes", {
        method: "POST",
        json: {
          nom: answers.nom,
          identifiant: answers.identifiant,
          tailscale: answers.tailscale,
          rang: answers.rang || "admin",
        },
      });
      if (data.mot_de_passe) await showTemporaryPassword(data.compte, data.mot_de_passe);
      else toast(`${data.compte.nom} ajouté : son mot de passe provisoire s'affichera sur son appli`);
    } catch (error) {
      toast(error.message, true);
    }
    reload();
  };
  content.querySelectorAll(".btn-menu").forEach((element) =>
    element.addEventListener("click", async () => {
      const item = data.comptes[+element.dataset.i];
      const action = async (body, ok) => {
        try {
          await api(`/api/comptes/${encodeURIComponent(item.id)}`, { method: "POST", json: body });
          toast(ok);
        } catch (error) {
          toast(error.message, true);
        }
        reload();
      };
      const choices = [
        {
          texte: "Renommer",
          f: async () => {
            const name = await confirmAction({ titre: "Nom affiché", champ: item.nom, valider: "Renommer" });
            if (name) action({ action: "nom", nom: name }, "Renommé");
          },
        },
        {
          texte: item.tailscale ? "Changer le compte Tailscale" : "Relier un compte Tailscale",
          f: async () => {
            const value = await confirmAction({
              titre: "Compte Tailscale",
              texte:
                "L'adresse avec laquelle la personne se connecte à Tailscale. Vide = plus d'accès à l'appli par ce compte.",
              champ: item.tailscale || "",
              valider: "Enregistrer",
            });
            if (value !== null) action({ action: "tailscale", tailscale: value }, "Enregistré");
          },
        },
      ];
      if (superAdmin)
        choices.push({
          texte: item.rang === "super" ? "Passer admin" : "Nommer super admin",
          f: () => action({ action: "rang", rang: item.rang === "super" ? "admin" : "super" }, "Rang changé"),
        });

      choices.push({
        texte: "Retirer ce compte",
        danger: true,
        f: async () => {
          if (
            await confirmAction({
              titre: `Retirer ${item.nom} ?`,
              texte: "Il ne pourra plus faire d'action admin" + (item.tailscale ? " ni ouvrir l'appli" : "") + ".",
              valider: "Retirer",
              danger: true,
            })
          )
            action({ action: "supprimer" }, "Compte retiré");
        },
      });
      const entry = await chooseFromMenu(`${item.nom} · ${ROLE_NAMES[item.rang]}`, choices);
      if (entry) setTimeout(entry.f, 50);
    }),
  );
};

async function showTemporaryPassword(account, password) {
  await new Promise((resolve) => {
    dialogForm.innerHTML = `<h2>Mot de passe provisoire</h2>
      <p>Donne-le à <strong>${escapeHtml(account.nom)}</strong> (identifiant <strong>${escapeHtml(account.identifiant)}</strong>). Il ne sera plus affiché, il sert une fois dans les 24 h, puis la personne choisit le sien.</p>
      <p class="code-secours">${escapeHtml(password)}</p>
      <div class="actions"><button class="btn btn-p" value="ok" type="submit">C'est noté</button></div>`;
    dialogElement.onclose = () => resolve();
    dialogElement.showModal();
  });
}
