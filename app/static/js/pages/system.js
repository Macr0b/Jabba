// Écrans Système et Plus : l'état du serveur (allumé depuis, courant, journal, appareils), sa mise à jour
// et son redémarrage. Ce qui se règle est dans l'écran Réglages (pages/settings.js).
//
// Sommaire :
//   partagé : pages.systeme (écran), pages.plus (écran)
//   interne : powerText, consumptionText, appUpdateCard
import { confirmAction, toast } from "../components/dialogs.js";
import { logOut } from "../core/admin_session.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatDuration, timeAgo } from "../core/format.js";
import { pages } from "../core/pages.js";
import { state } from "../core/state.js";
import { loadingHtml } from "../core/tasks.js";
import { activityLine } from "./activity.js";
import { t } from "../core/i18n.js";

pages.systeme = async () => {
  content.innerHTML = t`<div class="entete"><div><h1>Système</h1></div></div>${loadingHtml()}`;
  const item = await api("/api/systeme");
  content.innerHTML = t`
    <div class="entete"><div><h1>Système</h1><p class="sous-titre">${escapeHtml(item.name)} · ${escapeHtml(item.os)}</p></div>
      <div class="actions"><a class="btn" href="#/sauvegardes">Sauvegardes</a><button class="btn btn-danger" id="reboot">Redémarrer le serveur</button></div></div>
    ${item.redemarrage_requis ? `<div class="bandeau"><span>${item.restart && item.restart.mode === "manuel" ? t("Une mise à jour attend un redémarrage. Fais-le quand tu es devant le serveur, avec le bouton : si la machine bloque au démarrage, tu pourras agir.") : t("Une mise à jour attend un redémarrage. Il se fera seul à 4 h, ou maintenant avec le bouton.")}</span></div>` : ""}
    <div class="grille g2">
      <section class="carte pad"><dl class="cles">
        <dt>Allumé depuis</dt><dd>${formatDuration(item.duration)}</dd>
        <dt>Mises à jour auto</dt><dd>${item.last_auto_update ? t("Dernier passage le ") + escapeHtml(item.last_auto_update.replace(" ", " à ")) : t("Pas encore de passage")}</dd>
        <dt>Système</dt><dd>${escapeHtml(item.os)}</dd>
        <dt>Noyau</dt><dd>${escapeHtml(item.noyau)}</dd>
        <dt>Appli Jabba</dt><dd>version ${escapeHtml(item.app_version)}</dd>
        <dt>Courant</dt><dd>${powerText(item.power)}</dd>
        ${item.power && item.power.battery != null ? t`<dt>Consommation</dt><dd>${consumptionText(item.power, item.consumption)}</dd>` : ""}
      </dl></section>
      <section class="carte pad" id="maj"><h2>Appli Jabba</h2><div class="squelette" style="margin-top:10px"></div></section>
      <section class="carte pad"><div class="entete-carte"><h2>Journal</h2><a class="btn btn-petit" href="#/journal">Tout voir</a></div>
        <div class="journal" id="journal-court"><div class="squelette"></div></div></section>
      <section class="carte pad"><h2>Appareils Tailscale</h2>
        <div style="margin-top:10px">${
          item.devices
            .map(
              (
                item,
              ) => `<div class="appareil"><span>${escapeHtml(item.name)} <span class="note">· ${escapeHtml(item.os)}</span></span>
          <span class="pastille ${item.online ? "p-ok" : "p-gris"}">${item.online ? t("En ligne") : t("Hors ligne")}</span></div>`,
            )
            .join("") || t('<p class="note">Aucun autre appareil.</p>')
        }</div>
      </section>
    </div>`;
  appUpdateCard().catch((entry) => {
    const element = $("#maj");
    if (element) element.innerHTML = t`<h2>Appli Jabba</h2><p class="note">${escapeHtml(entry.message)}</p>`;
  });
  api("/api/journal?n=8")
    .then((events) => {
      const element = $("#journal-court");
      if (element)
        element.innerHTML = events.map(activityLine).join("") || t('<p class="note">Rien pour l\'instant.</p>');
    })
    .catch(() => {});
  $("#reboot").onclick = async () => {
    if (
      !(await confirmAction({
        title: t("Redémarrer le serveur ?"),
        text: t("Tous les outils seront coupés environ 1 minute."),
        confirmLabel: t("Redémarrer"),
        danger: true,
      }))
    )
      return;
    try {
      await api("/api/systeme/redemarrer", { method: "POST" });
      toast(t("Le serveur redémarre. Recharge la page dans une minute."));
    } catch (error) {
      toast(error.message, true);
    }
  };
};

pages.plus = async () => {
  content.innerHTML = t`<div class="entete"><div><h1>Plus</h1></div></div>
    <section class="carte liste-raccourcis">
      <a href="#/disques"><span>Disques</span><span>›</span></a>
      <a href="#/corbeille"><span>Corbeille</span><span>›</span></a>
      <a href="#/systeme"><span>Système</span><span>›</span></a>
      <a href="#/reglages"><span>Réglages</span><span>›</span></a>
      <a href="#/sauvegardes"><span>Sauvegardes</span><span>›</span></a>
      <a href="#/nuage"><span>Comptes Nextcloud</span><span>›</span></a>
      <a href="#/utilisateurs"><span>Admins de Jabba</span><span>›</span></a>
      <a href="#/invites"><span>Invités</span><span>›</span></a>
      <a href="#" id="sortir-mobile"><span>Se déconnecter</span><span>›</span></a>
      <a href="#/journal"><span>Journal</span><span>›</span></a>
      <a href="#/aide"><span>Aide</span><span>›</span></a>
    </section><p class="note" id="qui-mobile"></p>`;
  if (state.me && state.me.admin)
    $("#qui-mobile").textContent =
      t`Connecté en ${state.me.admin.rank === "super" ? t("super admin") : "admin"} : ${state.me.admin.username} · version ${state.me.version}`;
  $("#sortir-mobile").onclick = (event) => {
    event.preventDefault();
    logOut();
  };
};

// Redémarrage après une mise à jour du système : seul à 4 h, ou à la main (machine capricieuse au démarrage).
// Ligne « Courant » : secteur ou batterie, et la batterie du portable ou de l'onduleur.
function powerText(power) {
  if (!power || power.mains == null) return t("Pas encore mesuré");
  const onUps = power.source === "onduleur";
  const where = power.mains
    ? t("Sur secteur")
    : onUps
      ? t`<strong style="color:var(--rouge)">Sur l'onduleur</strong>`
      : t`<strong style="color:var(--rouge)">Sur batterie</strong>`;
  if (power.battery == null) return where;
  return where + (onUps ? t` · onduleur ${power.battery} %` : t` · batterie ${power.battery} %`);
}

// Ligne « Consommation » : l'onduleur la mesure tout le temps ; la batterie du portable, seulement sur batterie.
function consumptionText(power, consumption) {
  const onUps = power.source === "onduleur";
  if (!consumption || !consumption.latest)
    return onUps
      ? t`<span class="note">Pas encore mesurée.</span>`
      : t`<span class="note">Se mesure sur batterie : débranche le chargeur 2 minutes pour la voir.</span>`;
  const when =
    onUps || !power.mains
      ? t("en ce moment")
      : t("dernière mesure sur batterie, ") + escapeHtml(timeAgo(consumption.latest.t));
  const lowest = consumption.idle
    ? t`<br><span class="note">Au plus bas : ${String(consumption.idle.w).replace(".", ",")} W, au repos</span>`
    : "";
  const box = onUps
    ? t`<br><span class="note">Mesurée par l'onduleur : tout ce qui y est branché (la box aussi).</span>`
    : "";
  return `${String(consumption.latest.w).replace(".", ",")} W <span class="note">(${when})</span>${lowest}${box}`;
}

// ------------------------------------------------------------ mise à jour de l'appli
async function appUpdateCard(check = false) {
  const element = $("#maj");
  if (!element) return;
  const data = await api("/api/appli/maj" + (check ? "?verifier=1" : ""));
  let body;
  if (!data.repository)
    body = t`<p class="note">Pas encore reliée à GitHub : les mises à jour se feront ici en un clic une fois le dépôt réglé.</p>`;
  else if (!data.ready)
    body = t`<p class="note">Le dépôt est réglé (${escapeHtml(data.repository)}), mais le dossier de l'appli sur le serveur n'est pas encore relié à GitHub.</p>`;
  else if (data.available)
    body = t`<div class="bandeau" style="margin-bottom:10px"><span><strong>Version ${escapeHtml(data.latest.version)} disponible</strong> (tu as la ${escapeHtml(data.current)}).</span></div>
      ${data.latest.notes ? `<pre class="notes-version">${escapeHtml(data.latest.notes)}</pre>` : ""}
      <div class="actions"><button class="btn btn-p" id="installer">Installer la ${escapeHtml(data.latest.version)}</button></div>`;
  else
    body = t`<p class="note" style="margin-bottom:10px">À jour : version ${escapeHtml(data.current)}${data.latest ? "" : t(" (GitHub n'a pas répondu)")}.</p><div class="actions"><button class="btn" id="verifier">Vérifier maintenant</button></div>`;
  element.innerHTML = t`<h2>Appli Jabba</h2><div style="margin-top:8px">${body}</div>
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
            (item) => t`<div class="version">
        <div class="entete-version"><strong>Version ${escapeHtml(item.version)}</strong>${item.version === data.current ? t(' <span class="pastille p-ok">Installée</span>') : ""}<span class="note">${escapeHtml(new Date(item.date + "T12:00:00").toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" }))}</span></div>
        <ul>${item.changes.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`,
          )
          .join("") || t('<p class="note">Pas d\'historique.</p>');
    } catch (error) {
      $("#versions").innerHTML = `<p class="note">${escapeHtml(error.message)}</p>`;
    }
  });
  const button = $("#verifier");
  const installButton = $("#installer");
  if (button)
    button.onclick = () => {
      button.disabled = true;
      appUpdateCard(true).catch((entry) => toast(entry.message, true));
    };
  if (installButton)
    installButton.onclick = async () => {
      if (
        !(await confirmAction({
          title: t`Installer la version ${data.latest.version} ?`,
          text: t(
            "L'appli se coupe 1 à 3 minutes pendant l'installation, puis se recharge toute seule. Les outils, tes fichiers et tes réglages ne sont pas touchés.",
          ),
          confirmLabel: t("Installer"),
        }))
      )
        return;
      try {
        await api("/api/appli/maj", { method: "POST" });
        const start = Date.now();
        element.innerHTML = t`<h2>Appli Jabba</h2>
        <p style="margin:8px 0 6px"><strong>Installation de la ${escapeHtml(data.latest.version)}</strong></p>
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
            if (result.version === data.latest.version) {
              render(t("Installée ! Rechargement…"), 100);
              percent = 100;
              element.querySelector(".barre-maj span").style.width = "100%";
              setTimeout(() => location.reload(), 800);
              return;
            }
            const info = await api("/api/appli/maj/etat");
            if (info.status === "echec") {
              element.innerHTML = t`<h2>Appli Jabba</h2>
              <div class="erreur" style="margin-top:8px"><strong>La version ${escapeHtml(info.version)} ne s'est pas installée.</strong> Ta version actuelle continue de fonctionner normalement.</div>
              ${info.detail && info.detail.length ? `<pre class="notes-version">${escapeHtml(info.detail.join("\n"))}</pre>` : ""}
              <div class="actions"><button class="btn btn-p" id="reessayer-maj">Réessayer</button></div>`;
              $("#reessayer-maj").onclick = () => appUpdateCard(true).catch((error) => toast(error.message, true));
              return;
            }
            const minutes = Math.floor((Date.now() - start) / 60000);
            render(
              t`${info.step || t("Installation en cours")}${minutes ? ` · ${minutes} min` : ""} — jusqu'à 10 minutes sur un petit serveur. Ne ferme pas cette page.`,
              info.percent,
            );
          } catch (_) {
            render(t("Redémarrage de l'appli…"), 94);
          }
        }
        toast(t("L'installation prend plus de temps que prévu. Recharge la page dans quelques minutes."), true);
      } catch (error) {
        toast(error.message, true);
      }
    };
}
