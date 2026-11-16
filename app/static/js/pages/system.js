// Écrans Système et Plus : état du serveur, redémarrage, mises à jour, sécurité.
//
// Sommaire :
//   partagé : pages.systeme (écran), pages.plus (écran)
//   interne : restartPolicyCard, appUpdateCard (cartes Double vérification et Serveur ouvert au public : components/)
import { alertsCard } from "../components/alerts.js";
import { confirmAction, toast } from "../components/dialogs.js";
import { appearanceCard } from "../components/appearance_card.js";
import { publicModeCard } from "../components/public_mode_card.js";
import { secondFactorCard } from "../components/second_factor.js";
import { chooseMyPassword, logOut, showRecoveryCode } from "../core/admin_session.js";
import { api, formBody } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatDuration, timeAgo } from "../core/format.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { state } from "../core/state.js";
import { loadingHtml } from "../core/tasks.js";
import { activityLine } from "./activity.js";

pages.systeme = async () => {
  content.innerHTML = `<div class="entete"><div><h1>Système</h1></div></div>${loadingHtml()}`;
  const [item, entry] = await Promise.all([api("/api/systeme"), api("/api/admin/etat")]);
  content.innerHTML = `
    <div class="entete"><div><h1>Système</h1><p class="sous-titre">${escapeHtml(item.nom)} · ${escapeHtml(item.os)}</p></div>
      <div class="actions"><a class="btn" href="#/sauvegardes">Sauvegardes</a>${state.admin && state.admin.compte_serveur ? '<a class="btn" href="#/terminal">Terminal</a>' : ""}<button class="btn btn-danger" id="reboot">Redémarrer le serveur</button></div></div>
    ${item.redemarrage_requis ? `<div class="bandeau"><span>${item.redemarrage && item.redemarrage.mode === "manuel" ? "Une mise à jour attend un redémarrage. Fais-le quand tu es devant le serveur, avec le bouton : si la machine bloque au démarrage, tu pourras agir." : "Une mise à jour attend un redémarrage. Il se fera seul à 4 h, ou maintenant avec le bouton."}</span></div>` : ""}
    <div class="grille g2">
      <section class="carte pad"><dl class="cles">
        <dt>Allumé depuis</dt><dd>${formatDuration(item.duree)}</dd>
        <dt>Mises à jour auto</dt><dd>${item.derniere_maj_auto ? "Dernier passage le " + escapeHtml(item.derniere_maj_auto.replace(" ", " à ")) : "Pas encore de passage"}</dd>
        <dt>Système</dt><dd>${escapeHtml(item.os)}</dd>
        <dt>Noyau</dt><dd>${escapeHtml(item.noyau)}</dd>
        <dt>Appli Jabba</dt><dd>version ${escapeHtml(item.version_appli)}</dd>
        <dt>Courant</dt><dd>${
          item.courant && item.courant.secteur != null
            ? (item.courant.secteur ? "Sur secteur" : `<strong style="color:var(--rouge)">Sur batterie</strong>`) +
              (item.courant.batterie != null ? ` · batterie ${item.courant.batterie} %` : "")
            : "Pas encore mesuré"
        }</dd>
        ${
          item.courant && item.courant.batterie != null
            ? `<dt>Consommation</dt><dd>${
                item.conso && item.conso.derniere
                  ? `${String(item.conso.derniere.w).replace(".", ",")} W <span class="note">(${item.courant.secteur ? "dernière mesure sur batterie, " + escapeHtml(timeAgo(item.conso.derniere.t)) : "en ce moment"})</span>${item.conso.repos ? `<br><span class="note">Au plus bas : ${String(item.conso.repos.w).replace(".", ",")} W, au repos</span>` : ""}`
                  : `<span class="note">Se mesure sur batterie : débranche le chargeur 2 minutes pour la voir.</span>`
              }</dd>`
            : ""
        }
      </dl></section>
      <section class="carte pad" id="maj"><h2>Appli Jabba</h2><div class="squelette" style="margin-top:10px"></div></section>
      <section class="carte pad"><div class="entete-carte"><h2>Sécurité</h2><a class="btn btn-petit" href="#/utilisateurs">Admins de Jabba</a></div>
        <p class="note" id="connecte-admin">${entry.connecte ? `Connecté en admin : <strong>${escapeHtml(entry.connecte.identifiant)}</strong> · ${entry.connecte.rang === "super" ? "super admin" : "admin"}` : "Pas connecté en admin."}</p>
        <p class="note" style="margin:8px 0 14px">Le mot de passe du serveur protège les actions sensibles : activer un outil, mettre à jour, redémarrer, régler ou oublier un disque, supprimer un dossier entier, effacer la corbeille.</p>
        ${entry.code_existe ? "" : `<div class="bandeau" style="margin-bottom:12px"><span>Aucun code de secours : crée-le maintenant, il sert si tu oublies le mot de passe.</span></div>`}
        <div class="actions"><button class="btn" id="changer-mdp">${entry.connecte && entry.connecte.identifiant !== entry.serveur ? "Changer mon mot de passe" : "Changer le mot de passe du serveur"}</button>
        <button class="btn" id="code-secours">${entry.code_existe ? "Nouveau code de secours" : "Créer le code de secours"}</button></div>
      </section>
      <section class="carte pad" id="apparence"><h2>Apparence</h2></section>
      <section class="carte pad" id="double"><h2>Double vérification</h2><div class="squelette" style="margin-top:10px"></div></section>
      <section class="carte pad" id="public"><h2>Serveur ouvert au public</h2><div class="squelette" style="margin-top:10px"></div></section>
      <section class="carte pad" id="alertes"><h2>Alertes</h2><div class="squelette" style="margin-top:10px"></div></section>
      <section class="carte pad" id="redem"><h2>Redémarrage après une mise à jour</h2><div class="squelette" style="margin-top:10px"></div></section>
      <section class="carte pad"><div class="entete-carte"><h2>Journal</h2><a class="btn btn-petit" href="#/journal">Tout voir</a></div>
        <div class="journal" id="journal-court"><div class="squelette"></div></div></section>
      <section class="carte pad"><h2>Appareils Tailscale</h2>
        <div style="margin-top:10px">${
          item.appareils
            .map(
              (
                item,
              ) => `<div class="appareil"><span>${escapeHtml(item.nom)} <span class="note">· ${escapeHtml(item.os)}</span></span>
          <span class="pastille ${item.en_ligne ? "p-ok" : "p-gris"}">${item.en_ligne ? "En ligne" : "Hors ligne"}</span></div>`,
            )
            .join("") || '<p class="note">Aucun autre appareil.</p>'
        }</div>
      </section>
    </div>`;
  appearanceCard();
  secondFactorCard().catch((entry) => {
    const element = $("#double");
    if (element) element.innerHTML = `<h2>Double vérification</h2><p class="note">${escapeHtml(entry.message)}</p>`;
  });
  publicModeCard().catch((entry) => {
    const element = $("#public");
    if (element)
      element.innerHTML = `<h2>Serveur ouvert au public</h2><p class="note">${escapeHtml(entry.message)}</p>`;
  });
  alertsCard().catch((entry) => {
    const element = $("#alertes");
    if (element) element.innerHTML = `<h2>Alertes</h2><p class="note">${escapeHtml(entry.message)}</p>`;
  });
  restartPolicyCard().catch((entry) => {
    const element = $("#redem");
    if (element)
      element.innerHTML = `<h2>Redémarrage après une mise à jour</h2><p class="note">${escapeHtml(entry.message)}</p>`;
  });
  appUpdateCard().catch((entry) => {
    const element = $("#maj");
    if (element) element.innerHTML = `<h2>Appli Jabba</h2><p class="note">${escapeHtml(entry.message)}</p>`;
  });
  api("/api/journal?n=8")
    .then((events) => {
      const element = $("#journal-court");
      if (element) element.innerHTML = events.map(activityLine).join("") || '<p class="note">Rien pour l\'instant.</p>';
    })
    .catch(() => {});
  $("#changer-mdp").onclick = async () => {
    if (entry.connecte && entry.connecte.identifiant !== entry.serveur) {
      await chooseMyPassword(false);
      return;
    }
    const answers = await formBody({
      titre: "Changer le mot de passe du serveur",
      texte: "C'est aussi le mot de passe de sudo dans le Terminal.",
      champs: [
        { nom: "identifiant", cache: true, valeur: state.serverAccount, autocomplete: "username" },
        { nom: "ancien", type: "password", label: "Mot de passe actuel", autocomplete: "current-password" },
        {
          nom: "nouveau",
          type: "password",
          label: "Nouveau mot de passe (15 caractères minimum, une phrase de quelques mots convient)",
          autocomplete: "new-password",
        },
        {
          nom: "confirmation",
          type: "password",
          label: "Nouveau mot de passe, encore une fois",
          autocomplete: "new-password",
        },
      ],
      valider: "Changer",
    });
    if (!answers || answers === "secondaire") return;
    if (answers.nouveau !== answers.confirmation) return toast("Les deux mots de passe ne sont pas identiques.", true);
    try {
      await api("/api/admin/mot-de-passe", {
        method: "POST",
        json: { ancien: answers.ancien, nouveau: answers.nouveau },
      });
      toast("Mot de passe changé : reconnecte-toi");
      setTimeout(() => location.reload(), 1500);
    } catch (error) {
      toast(error.message, true);
    }
  };
  $("#code-secours").onclick = async () => {
    if (
      entry.code_existe &&
      !(await confirmAction({
        titre: "Créer un nouveau code de secours ?",
        texte: "L'ancien code ne marchera plus.",
        valider: "Créer",
      }))
    )
      return;
    try {
      const result = await api("/api/admin/code", { method: "POST" });
      await showRecoveryCode(result.code);
      pages.systeme().catch(showError);
    } catch (error) {
      toast(error.message, true);
    }
  };
  $("#reboot").onclick = async () => {
    if (
      !(await confirmAction({
        titre: "Redémarrer le serveur ?",
        texte: "Tous les outils seront coupés environ 1 minute.",
        valider: "Redémarrer",
        danger: true,
      }))
    )
      return;
    try {
      await api("/api/systeme/redemarrer", { method: "POST" });
      toast("Le serveur redémarre. Recharge la page dans une minute.");
    } catch (error) {
      toast(error.message, true);
    }
  };
};

pages.plus = async () => {
  content.innerHTML = `<div class="entete"><div><h1>Plus</h1></div></div>
    <section class="carte liste-raccourcis">
      <a href="#/disques"><span>Disques</span><span>›</span></a>
      <a href="#/corbeille"><span>Corbeille</span><span>›</span></a>
      <a href="#/systeme"><span>Système et alertes</span><span>›</span></a>
      <a href="#/sauvegardes"><span>Sauvegardes</span><span>›</span></a>
      <a href="#/nuage"><span>Comptes Nextcloud</span><span>›</span></a>
      <a href="#/utilisateurs"><span>Admins de Jabba</span><span>›</span></a>
      <a href="#/invites"><span>Invités</span><span>›</span></a>
      <a href="#" id="sortir-mobile"><span>Se déconnecter</span><span>›</span></a>
      <a href="#/journal"><span>Journal</span><span>›</span></a>
      ${state.admin && state.admin.compte_serveur ? '<a href="#/terminal"><span>Terminal</span><span>›</span></a>' : ""}
      <a href="#/aide"><span>Aide</span><span>›</span></a>
    </section><p class="note" id="qui-mobile"></p>`;
  if (state.me && state.me.admin)
    $("#qui-mobile").textContent =
      `Connecté en ${state.me.admin.rang === "super" ? "super admin" : "admin"} : ${state.me.admin.identifiant} · version ${state.me.version}`;
  $("#sortir-mobile").onclick = (event) => {
    event.preventDefault();
    logOut();
  };
};

// Redémarrage après une mise à jour du système : seul à 4 h, ou à la main (machine capricieuse au démarrage).
async function restartPolicyCard() {
  const element = $("#redem");
  if (!element) return;
  const data = await api("/api/systeme/redemarrage");
  const superAdmin = state.admin && state.admin.rang === "super";
  const automatic = data.mode === "auto";
  element.innerHTML = `<h2>Redémarrage après une mise à jour</h2>
    <p class="note" style="margin:8px 0">Certaines mises à jour du système ne prennent effet qu'au redémarrage. Le serveur peut le faire seul la nuit, ou te laisser le faire quand tu es devant : conseillé si la machine a déjà bloqué au démarrage.</p>
    <p style="margin:10px 0"><span class="pastille ${automatic ? "p-ok" : "p-gris"}">${automatic ? "Seul, à 4 h" : "À la main"}</span>
      <span class="note">${automatic ? "Le serveur redémarre seul à 4 h quand une mise à jour le demande." : "Jabba te prévient ; tu redémarres avec le bouton « Redémarrer le serveur »."}${data.choisi ? "" : " (réglage d'origine, pas encore choisi)"}</span></p>
    ${superAdmin ? `<div class="actions"><button class="btn" id="redem-changer">${automatic ? "Passer à la main" : "Redémarrer seul à 4 h"}</button></div>` : ""}`;
  const button = $("#redem-changer");
  if (button)
    button.onclick = async () => {
      const to = automatic ? "manuel" : "auto";
      const ok = await confirmAction(
        to === "auto"
          ? {
              titre: "Redémarrer seul à 4 h ?",
              texte:
                "Quand une mise à jour le demande, le serveur redémarrera seul à 4 h du matin. À éviter si la machine a déjà bloqué au démarrage : personne ne serait là pour la relancer.",
              valider: "Redémarrer seul",
            }
          : {
              titre: "Redémarrer à la main ?",
              texte:
                "Le serveur ne redémarrera plus seul. Jabba te préviendra quand une mise à jour attend : redémarre alors quand tu es devant. Les correctifs déjà installés restent actifs, sauf ceux du noyau qui attendent le redémarrage.",
              valider: "À la main",
            },
      );
      if (!ok) return;
      try {
        await api("/api/systeme/redemarrage", { method: "POST", json: { mode: to } });
        toast(to === "auto" ? "Redémarrage automatique à 4 h" : "Redémarrage à la main");
      } catch (error) {
        toast(error.message, true);
      }
      restartPolicyCard().catch(() => {});
    };
}

// ------------------------------------------------------------ mise à jour de l'appli
async function appUpdateCard(check = false) {
  const element = $("#maj");
  if (!element) return;
  const data = await api("/api/appli/maj" + (check ? "?verifier=1" : ""));
  let body;
  if (!data.depot)
    body = `<p class="note">Pas encore reliée à GitHub : les mises à jour se feront ici en un clic une fois le dépôt réglé.</p>`;
  else if (!data.pret)
    body = `<p class="note">Le dépôt est réglé (${escapeHtml(data.depot)}), mais le dossier de l'appli sur le serveur n'est pas encore relié à GitHub.</p>`;
  else if (data.disponible)
    body = `<div class="bandeau" style="margin-bottom:10px"><span><strong>Version ${escapeHtml(data.derniere.version)} disponible</strong> (tu as la ${escapeHtml(data.actuelle)}).</span></div>
      ${data.derniere.notes ? `<pre class="notes-version">${escapeHtml(data.derniere.notes)}</pre>` : ""}
      <div class="actions"><button class="btn btn-p" id="installer">Installer la ${escapeHtml(data.derniere.version)}</button></div>`;
  else
    body = `<p class="note" style="margin-bottom:10px">À jour : version ${escapeHtml(data.actuelle)}${data.derniere ? "" : " (GitHub n'a pas répondu)"}.</p><div class="actions"><button class="btn" id="verifier">Vérifier maintenant</button></div>`;
  element.innerHTML = `<h2>Appli Jabba</h2><div style="margin-top:8px">${body}</div>
    <details class="historique"><summary>Historique des versions</summary><div id="versions"><div class="squelette"></div></div></details>`;
  const details = element.querySelector(".historique");
  details.addEventListener("toggle", async () => {
    if (!details.open || details.dataset.charge) return;
    details.dataset.charge = "1";
    try {
      const versions = await api("/api/appli/versions");
      $("#versions").innerHTML =
        versions
          .map(
            (item) => `<div class="version">
        <div class="entete-version"><strong>Version ${escapeHtml(item.version)}</strong>${item.version === data.actuelle ? ' <span class="pastille p-ok">Installée</span>' : ""}<span class="note">${escapeHtml(new Date(item.date + "T12:00:00").toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" }))}</span></div>
        <ul>${item.changements.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`,
          )
          .join("") || '<p class="note">Pas d\'historique.</p>';
    } catch (error) {
      $("#versions").innerHTML = `<p class="note">${escapeHtml(error.message)}</p>`;
    }
  });
  const button = $("#verifier"),
    installButton = $("#installer");
  if (button)
    button.onclick = () => {
      button.disabled = true;
      appUpdateCard(true).catch((entry) => toast(entry.message, true));
    };
  if (installButton)
    installButton.onclick = async () => {
      if (
        !(await confirmAction({
          titre: `Installer la version ${data.derniere.version} ?`,
          texte:
            "L'appli se coupe 1 à 3 minutes pendant l'installation, puis se recharge toute seule. Les outils, tes fichiers et tes réglages ne sont pas touchés.",
          valider: "Installer",
        }))
      )
        return;
      try {
        await api("/api/appli/maj", { method: "POST" });
        const start = Date.now();
        element.innerHTML = `<h2>Appli Jabba</h2>
        <p style="margin:8px 0 6px"><strong>Installation de la ${escapeHtml(data.derniere.version)}</strong></p>
        <div class="barre-maj" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="3"><span style="width:3%"></span></div>
        <p class="note" id="maj-etape">Préparation…</p>`;
        let percent = 3;
        const render = (value, amount = null) => {
          // jamais de recul ; entre deux étapes, la barre avance doucement d'elle-même
          percent = Math.min(97, Math.max(percent + 0.4, amount ?? 0));
          const node = element.querySelector(".barre-maj");
          if (!node) return;
          node.setAttribute("aria-valuenow", String(Math.round(percent)));
          node.firstElementChild.style.width = percent + "%";
          $("#maj-etape").textContent = value;
        };
        while (Date.now() - start < 20 * 60000) {
          await new Promise((result) => setTimeout(result, 3000));
          try {
            const result = await api("/api/moi");
            if (result.version === data.derniere.version) {
              render("Installée ! Rechargement…", 100);
              percent = 100;
              element.querySelector(".barre-maj span").style.width = "100%";
              setTimeout(() => location.reload(), 800);
              return;
            }
            const info = await api("/api/appli/maj/etat");
            if (info.statut === "echec") {
              element.innerHTML = `<h2>Appli Jabba</h2>
              <div class="erreur" style="margin-top:8px"><strong>La version ${escapeHtml(info.version)} ne s'est pas installée.</strong> Ta version actuelle continue de fonctionner normalement.</div>
              ${info.detail && info.detail.length ? `<pre class="notes-version">${escapeHtml(info.detail.join("\n"))}</pre>` : ""}
              <div class="actions"><button class="btn btn-p" id="reessayer-maj">Réessayer</button></div>`;
              $("#reessayer-maj").onclick = () => appUpdateCard(true).catch((error) => toast(error.message, true));
              return;
            }
            const minutes = Math.floor((Date.now() - start) / 60000);
            render(
              `${info.etape || "Installation en cours"}${minutes ? ` · ${minutes} min` : ""} — jusqu'à 10 minutes sur un petit serveur. Ne ferme pas cette page.`,
              info.pourcent,
            );
          } catch (_) {
            render("Redémarrage de l'appli…", 94);
          }
        }
        toast("L'installation prend plus de temps que prévu. Recharge la page dans quelques minutes.", true);
      } catch (error) {
        toast(error.message, true);
      }
    };
}
