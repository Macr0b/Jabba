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
import { t } from "../core/i18n.js";

// ------------------------------------------------------------ utilisateurs
const ROLE_NAMES = { super: t("Super admin"), admin: t("Admin") };

pages.utilisateurs = async () => {
  content.innerHTML = t`<div class="entete"><div><h1>Admins de Jabba</h1></div></div>${loadingHtml(4)}`;
  const data = await api("/api/comptes");
  const superAdmin = data.me.rank === "super";
  const byLogin = Object.fromEntries(
    data.accounts.filter((item) => item.tailscale).map((item) => [item.tailscale, item]),
  );
  content.innerHTML = t`
    <div class="entete"><div><h1>Admins de Jabba</h1><p class="sous-titre">Les admins de l'appli, et qui se connecte. Tu es <strong>${escapeHtml(data.me.username)}</strong> · ${ROLE_NAMES[data.me.rank]}.</p></div>
      <div class="actions"><a class="btn" href="#/nuage">Comptes Nextcloud</a><button class="btn btn-p" id="ajouter">Ajouter un admin</button></div></div>
    ${data.requests
      .map(
        (
          item,
        ) => t`<div class="bandeau demande"><span><strong>${escapeHtml(item.name)}</strong> (${escapeHtml(item.username)}) a oublié son mot de passe · demandé par ${escapeHtml(item.requester)}, ${escapeHtml(timeAgo(item.t))}.${item.other_person ? t(" <strong>Attention : demandé depuis le compte Tailscale de quelqu'un d'autre.</strong> Le mot de passe provisoire n'ira qu'à son compte à lui.") : ""}</span>
      <span class="actions"><button class="btn btn-petit btn-p" data-accepter="${escapeHtml(item.id)}">Accepter</button><button class="btn btn-petit" data-refuser="${escapeHtml(item.id)}">Refuser</button></span></div>`,
      )
      .join("")}
    ${!superAdmin && data.request_count ? t`<div class="bandeau"><span>${data.request_count} demande${data.request_count > 1 ? "s" : ""} de mot de passe en attente : seul un super admin peut y répondre.</span></div>` : ""}
    <section class="carte tableau" aria-label="Comptes admin">
      <div class="ligne titres"><span>Compte</span><span class="col-l">Rang</span><span class="col-l">Dernière connexion</span><span></span></div>
      ${data.accounts
        .map(
          (item, index) => `
        <div class="ligne">
          <span class="nom"><span class="avatar" aria-hidden="true">${escapeHtml((item.name || item.username)[0].toUpperCase())}</span>
            <span>${escapeHtml(item.name)}<span class="detail-chemin">${escapeHtml(item.username)}${item.system ? t(" · mot de passe du serveur") : item.tailscale ? t(" · Tailscale : ") + escapeHtml(item.tailscale) : t(" · sans compte Tailscale")}${item.temporary ? t(" · mot de passe provisoire") : ""}</span>
            <span class="mobile-detail">${ROLE_NAMES[item.rank]}</span></span></span>
          <span class="col-l"><span class="pastille ${item.rank === "super" ? "p-alerte" : "p-gris"}">${ROLE_NAMES[item.rank]}</span></span>
          <span class="gris col-l">${item.system ? "—" : escapeHtml(timeAgo(item.last_login))}</span>
          ${item.system ? "<span></span>" : t`<button class="btn-menu" data-i="${index}" aria-label="Actions pour ${escapeHtml(item.name)}">•••</button>`}
        </div>`,
        )
        .join("")}
    </section>
    <section class="carte pad">
      <label class="ligne-reglage"><span><strong>Espace ouvert à tous les admins</strong><br><span class="note">${data.settings.admin_space ? t("Tous les admins peuvent ajouter des comptes. Seul un super admin peut nommer ou retirer un super admin.") : t("Seul un super admin peut ouvrir cet espace.")}${superAdmin ? "" : t(" Réglé par le super admin.")}</span></span>
        <button class="interrupteur" role="switch" aria-checked="${data.settings.admin_space}" id="espace"${superAdmin ? "" : " disabled"}><span></span></button></label>
    </section>
    <section class="carte pad">
      <h2>Qui se connecte</h2>
      <p class="note" style="margin:6px 0 10px">Rempli tout seul par Tailscale : chaque personne qui ouvre l'appli, depuis quel appareil.</p>
      <div class="journal">${
        data.visits
          .map(
            (item) => `<div class="evenement">
          <span class="famille">${data.owners.includes(item.login) ? t("Propriétaire") : byLogin[item.login] ? ROLE_NAMES[byLogin[item.login].rank] : t("Accès")}</span>
          <span class="texte-ev">${escapeHtml(item.name)} <span class="note">· ${escapeHtml(item.login)}${item.device ? " · " + escapeHtml(item.device) : ""} · ${item.n || 1} visite${(item.n || 1) > 1 ? "s" : ""}</span></span>
          <span class="note quand-ev">${escapeHtml(timeAgo(item.latest))}</span></div>`,
          )
          .join("") || t('<p class="note">Personne pour l\'instant.</p>')
      }</div>
      ${
        data.refusals.length
          ? t`<h2 style="margin-top:16px">Accès refusés</h2><div class="journal">${data.refusals
              .map(
                (item) => t`<div class="evenement f-admin"><span class="famille">Refusé</span>
          <span class="texte-ev">${escapeHtml(item.login)}${item.device ? ` <span class="note">· ${escapeHtml(item.device)}</span>` : ""}</span><span class="note quand-ev">${escapeHtml(timeAgo(item.t))}</span></div>`,
              )
              .join("")}</div>`
          : ""
      }
      <p class="note" style="margin-top:10px">Pour donner l'accès à quelqu'un : ajoute-le comme admin avec son compte Tailscale. Les propriétaires sont ceux du fichier de réglages du serveur.</p>
    </section>
    <section class="carte pad"><h2>Appareils connectés</h2>
      <p class="note" style="margin:6px 0 10px">Chaque appareil reste connecté 30 jours. Déconnecte celui que tu as perdu ou prêté.${superAdmin ? "" : t(" Tu vois tes propres appareils.")}</p>
      <div class="journal" id="appareils"><div class="squelette"></div></div></section>`;
  api("/api/sessions")
    .then((seconds) => {
      const element = $("#appareils");
      if (!element) return;
      element.innerHTML =
        seconds
          .map(
            (item) => t`<div class="evenement"><span class="famille">${escapeHtml(item.username)}</span>
      <span class="texte-ev">${escapeHtml(item.device || t("Appareil inconnu"))} <span class="note">· connecté ${escapeHtml(timeAgo(item.created))} · vu ${escapeHtml(timeAgo(item.seen))}</span></span>
      <span>${item.current ? t('<span class="pastille p-ok">Cet appareil</span>') : t`<button class="btn btn-petit" data-session="${escapeHtml(item.id)}">Déconnecter</button>`}</span></div>`,
          )
          .join("") || t('<p class="note">Aucun.</p>');
      element.querySelectorAll("[data-session]").forEach((element) =>
        element.addEventListener("click", async () => {
          try {
            await api(`/api/sessions/${encodeURIComponent(element.dataset.session)}/fermer`, { method: "POST" });
            toast(t("Appareil déconnecté"));
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
        toast(accept ? t("Acceptée : le mot de passe provisoire s'affichera sur son appareil") : t("Demande refusée"));
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
        await api("/api/comptes-reglages", { method: "POST", json: { admin_space: opened } });
      } catch (error) {
        toast(error.message, true);
      }
      reload();
    };
  $("#ajouter").onclick = async () => {
    const answers = await formBody({
      title: t("Ajouter un admin"),
      text: t(
        "Un mot de passe provisoire est créé. Avec son compte Tailscale, il s'affiche sur l'appli de la personne ; sinon, ici, pour que tu le lui donnes.",
      ),
      fields: [
        { name: "name", label: t("Nom affiché"), hint: t("Camille") },
        { name: "username", label: t("Identifiant de connexion"), hint: "camille" },
        {
          name: "tailscale",
          label: t("Compte Tailscale (facultatif)"),
          hint: t("adresse avec laquelle elle se connecte à Tailscale"),
        },
        ...(superAdmin
          ? [
              {
                name: "rank",
                label: t("Rang"),
                options: [
                  ["admin", t("Admin")],
                  ["super", t("Super admin")],
                ],
                value: "admin",
              },
            ]
          : []),
      ],
      confirmLabel: t("Ajouter"),
    });
    if (!answers || answers === "secondary") return;
    try {
      const data = await api("/api/comptes", {
        method: "POST",
        json: {
          name: answers.name,
          username: answers.username,
          tailscale: answers.tailscale,
          rank: answers.rank || "admin",
        },
      });
      if (data.password) await showTemporaryPassword(data.account, data.password);
      else toast(t`${data.account.name} ajouté : son mot de passe provisoire s'affichera sur son appli`);
    } catch (error) {
      toast(error.message, true);
    }
    reload();
  };
  content.querySelectorAll(".btn-menu").forEach((element) =>
    element.addEventListener("click", async () => {
      const item = data.accounts[+element.dataset.i];
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
          text: t("Renommer"),
          f: async () => {
            const name = await confirmAction({
              title: t("Nom affiché"),
              field: item.name,
              confirmLabel: t("Renommer"),
            });
            if (name) action({ action: "nom", name }, t("Renommé"));
          },
        },
        {
          text: item.tailscale ? t("Changer le compte Tailscale") : t("Relier un compte Tailscale"),
          f: async () => {
            const value = await confirmAction({
              title: t("Compte Tailscale"),
              text: t(
                "L'adresse avec laquelle la personne se connecte à Tailscale. Vide = plus d'accès à l'appli par ce compte.",
              ),
              field: item.tailscale || "",
              confirmLabel: t("Enregistrer"),
            });
            if (value !== null) action({ action: "tailscale", tailscale: value }, t("Enregistré"));
          },
        },
      ];
      if (superAdmin)
        choices.push({
          text: item.rank === "super" ? t("Passer admin") : t("Nommer super admin"),
          f: () => action({ action: "rang", rank: item.rank === "super" ? "admin" : "super" }, t("Rang changé")),
        });

      choices.push({
        text: t("Retirer ce compte"),
        danger: true,
        f: async () => {
          if (
            await confirmAction({
              title: t`Retirer ${item.name} ?`,
              text: t("Il ne pourra plus faire d'action admin") + (item.tailscale ? t(" ni ouvrir l'appli") : "") + ".",
              confirmLabel: t("Retirer"),
              danger: true,
            })
          )
            action({ action: "supprimer" }, t("Compte retiré"));
        },
      });
      const entry = await chooseFromMenu(`${item.name} · ${ROLE_NAMES[item.rank]}`, choices);
      if (entry) setTimeout(entry.f, 50);
    }),
  );
};

async function showTemporaryPassword(account, password) {
  await new Promise((resolve) => {
    dialogForm.innerHTML = t`<h2>Mot de passe provisoire</h2>
      <p>Donne-le à <strong>${escapeHtml(account.name)}</strong> (identifiant <strong>${escapeHtml(account.username)}</strong>). Il ne sera plus affiché, il sert une fois dans les 24 h, puis la personne choisit le sien.</p>
      <p class="code-secours">${escapeHtml(password)}</p>
      <div class="actions"><button class="btn btn-p" value="ok" type="submit">C'est noté</button></div>`;
    dialogElement.onclose = () => resolve();
    dialogElement.showModal();
  });
}
