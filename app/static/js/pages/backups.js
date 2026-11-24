// Écran Sauvegardes : réglages, copies, restauration.
//
// Sommaire :
//   partagé : pages.sauvegardes (écran)
//   interne : backupFollower, showPassphrase, backupStateHtml, snapshotList, ROOT_NAMES, browseSnapshot
import { confirmAction, dialogForm, dialogElement, toast } from "../components/dialogs.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { shortDuration, timeOf, timeAgo, dayOf, formatBytes, formatWhen } from "../core/format.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { state } from "../core/state.js";
import { loadingHtml } from "../core/tasks.js";
import { t } from "../core/i18n.js";

let backupFollower = null;

async function showPassphrase(passphrase, first) {
  await new Promise((resolve) => {
    dialogForm.innerHTML = t`<h2>${first ? t("La clé de tes sauvegardes") : t("Phrase de passe des sauvegardes")}</h2>
      <p>Tes sauvegardes sont chiffrées avec cette phrase. <strong>Sans elle, personne ne peut les relire, toi non plus</strong>, par exemple si le serveur est perdu et qu'il faut tout récupérer sur un autre ordinateur.</p>
      <p>${first ? t("Elle ne sera plus affichée d'elle-même : enregistre-la maintenant dans l'app Mots de passe et note-la sur papier, rangée ailleurs que le disque.") : t("Garde-la dans l'app Mots de passe et sur papier.")}</p>
      <p class="code-secours">${escapeHtml(passphrase)}</p>
      <div class="actions"><button class="btn" type="button" id="copier-phrase">Copier</button><button class="btn btn-p" value="ok" type="submit">C'est noté</button></div>`;
    dialogElement.onclose = () => resolve();
    dialogElement.showModal();
    $("#copier-phrase").onclick = async () => {
      try {
        await navigator.clipboard.writeText(passphrase);
        toast(t("Phrase copiée"));
      } catch (_) {
        toast(t("Copie impossible : sélectionne la phrase à la main."), true);
      }
    };
  });
}

function backupStateHtml(item) {
  if (item.in_progress)
    return t`<span class="pastille p-alerte">En cours</span> <span class="note">depuis ${timeOf(item.since)}</span>`;
  const result = item.last;
  if (!result) return t`<span class="pastille p-gris">Pas encore faite</span>`;
  if (!result.ok)
    return t`<span class="pastille p-rouge">Échouée</span> <span class="note">${escapeHtml(timeAgo(result.end))}</span><br><span style="color:var(--rouge)">${escapeHtml(result.error)}</span>`;
  return t`<span class="pastille p-ok">Réussie</span> <span class="note">${escapeHtml(timeAgo(result.end))} à ${timeOf(result.end)}, en ${shortDuration(result.duration)}</span>${result.warning ? `<br><span class="note">${escapeHtml(result.warning)}</span>` : ""}`;
}

pages.sauvegardes = async () => {
  if (backupFollower) {
    clearTimeout(backupFollower);
    backupFollower = null;
  }
  content.innerHTML = t`<div class="entete"><div><h1>Sauvegardes</h1></div></div>${loadingHtml()}`;
  const data = await api("/api/sauvegardes");
  const superAdmin = state.admin && state.admin.rank === "super";
  const diskPickerHtml = (selected) =>
    `<select id="sauv-disque">${data.disks.map((item) => `<option value="${escapeHtml(item.name)}"${item.name === selected ? " selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select>`;
  const what = t`<p class="note" style="margin:8px 0">Chaque nuit, une copie chiffrée part sur le disque choisi : tes dossiers (sans les films ni les téléchargements, qui existent ailleurs), les réglages des outils avec leurs bases, et ceux du serveur. Seules les nouveautés prennent de la place. On garde les ${data.garder.days} derniers jours, ${data.garder.weeks} semaines et ${data.garder.months} mois.</p>`;
  if (!data.ready) {
    content.innerHTML = t`<div class="entete"><div><h1>Sauvegardes</h1><p class="sous-titre">Pas encore en route.</p></div></div>
      <section class="carte pad"><h2>Mettre en route</h2>${what}
        <p class="note" style="margin:8px 0 14px">La copie est rangée dans un dossier caché du disque (<code>.sauvegardes-jabba</code>), à côté de ce qu'il contient déjà. Un disque en lecture seule n'est ouvert en écriture que le temps de la copie.</p>
        ${
          data.disks.length
            ? superAdmin
              ? t`<label class="note" for="sauv-disque">Disque des sauvegardes</label>${diskPickerHtml(data.disks[0].name)}
          <div class="actions" style="margin-top:14px"><button class="btn btn-p" id="sauv-activer">Mettre en route</button></div>`
              : t('<p class="note">Seul un super admin peut les mettre en route.</p>')
            : t(
                '<div class="bandeau"><span>Aucun disque externe : branche celui qui recevra les sauvegardes, il apparaîtra ici.</span></div>',
              )
        }
      </section>`;
    const button = $("#sauv-activer");
    if (button)
      button.onclick = async () => {
        button.disabled = true;
        button.textContent = t("Mise en route…");
        try {
          const data = await api("/api/sauvegardes/activer", {
            method: "POST",
            json: { disk: $("#sauv-disque").value },
          });
          if (data.phrase) await showPassphrase(data.phrase, true);
          toast(t("Sauvegardes en route : la première peut partir tout de suite"));
        } catch (error) {
          toast(error.message, true);
        }
        pages.sauvegardes().catch(showError);
      };
    return;
  }
  const result = data.last_success;
  content.innerHTML = t`
    <div class="entete"><div><h1>Sauvegardes</h1><p class="sous-titre">${data.active ? t`Chaque nuit à ${escapeHtml(data.hour)}, vers ${escapeHtml(data.disk)}` : t("En pause")}</p></div>
      <div class="actions"><button class="btn btn-p" id="sauv-lancer"${data.in_progress || !data.plugged ? " disabled" : ""}>${data.in_progress ? t("Sauvegarde en cours…") : t("Sauvegarder maintenant")}</button></div></div>
    ${data.plugged ? "" : t`<div class="bandeau"><span>Le disque ${escapeHtml(data.disk)} n'est pas branché. ${data.active ? t("La sauvegarde partira toute seule dès qu'il reviendra.") : ""}</span></div>`}
    <div class="grille g2">
      <section class="carte pad"><h2>Dernière sauvegarde</h2>
        <p style="margin:10px 0">${backupStateHtml(data)}</p>
        <dl class="cles">
          <dt>Dernière réussie</dt><dd>${result ? `${escapeHtml(dayOf(result.end))} à ${timeOf(result.end)}` : t("Aucune")}</dd>
          ${
            result && result.size != null
              ? t`<dt>Contenu</dt><dd>${formatBytes(result.size)} · ${(result.files || 0).toLocaleString("fr-FR")} fichiers</dd>
          <dt>Nouveautés copiées</dt><dd>${formatBytes(result.added)}${result.new_items != null ? t` <span class="note">(${result.new_items} nouveaux, ${result.modified_items} modifiés)</span>` : ""}</dd>`
              : ""
          }
          <dt>Prochaine</dt><dd>${data.next ? (data.next * 1000 - Date.now() < 86400000 ? (+data.hour.slice(0, 2) < 12 ? t("Cette nuit") : t("Aujourd'hui")) : escapeHtml(formatWhen(new Date(data.next * 1000).toISOString()))) + ` à ${escapeHtml(data.hour)}` : t("En pause")}</dd>
        </dl>
      </section>
      <section class="carte pad"><h2>Réglages</h2>${what}
        ${
          superAdmin
            ? t`
        <label class="ligne-reglage"><span><strong>Sauvegarde chaque nuit</strong></span>
          <button class="interrupteur" role="switch" aria-checked="${data.active}" id="sauv-actif" aria-label="Sauvegarde chaque nuit"><span></span></button></label>
        <label class="ligne-reglage"><span><strong>Heure</strong><br><span class="note">Une sauvegarde manquée (serveur éteint, disque absent) part dès que possible.</span></span>
          <input type="time" id="sauv-heure" value="${escapeHtml(data.hour)}" style="width:auto"></label>
        ${data.sources
          .map(
            (
              item,
            ) => `<label class="ligne-reglage"><span><strong>${escapeHtml(item.name)}</strong><br><span class="note">${escapeHtml(item.detail)}</span></span>
          <button class="interrupteur" role="switch" aria-checked="${item.active}" data-source="${escapeHtml(item.id)}" aria-label="${escapeHtml(item.name)}"><span></span></button></label>`,
          )
          .join("")}
        <label class="ligne-reglage"><span><strong>Disque</strong></span><span style="min-width:140px">${diskPickerHtml(data.disk)}</span></label>
        <div class="actions" style="margin-top:12px"><button class="btn" id="sauv-phrase">Revoir la phrase de passe</button></div>`
            : t`<p class="note">Seul un super admin peut changer ces réglages.</p>`
        }
      </section>
    </div>
    <section class="carte pad" id="sauv-copies" style="margin-top:16px"><div class="entete-carte"><h2>Retrouver un fichier</h2></div>
      <p class="note" style="margin:8px 0 12px">Choisis une date, puis le fichier ou le dossier à récupérer. Il revient dans <strong>Mes dossiers › Restaurations</strong> : rien n'est remplacé.</p>
      <div class="actions"><button class="btn" id="sauv-voir"${data.plugged ? "" : " disabled"}>Voir les sauvegardes</button></div>
    </section>`;
  $("#sauv-lancer").onclick = async () => {
    try {
      await api("/api/sauvegardes/lancer", { method: "POST" });
      toast(t("Sauvegarde lancée"));
    } catch (error) {
      toast(error.message, true);
    }
    pages.sauvegardes().catch(showError);
  };
  $("#sauv-voir").onclick = () => snapshotList().catch((entry) => toast(entry.message, true));
  if (data.in_progress)
    backupFollower = setTimeout(() => {
      if (location.hash.startsWith("#/sauvegardes")) pages.sauvegardes().catch(showError);
    }, 5000);
  if (!superAdmin) return;
  const save = async (json, whatElse) => {
    try {
      await api("/api/sauvegardes/reglages", { method: "POST", json });
      toast(whatElse);
    } catch (error) {
      toast(error.message, true);
    }
    pages.sauvegardes().catch(showError);
  };
  $("#sauv-actif").onclick = () =>
    save({ active: !data.active }, data.active ? t("Sauvegardes en pause") : t("Sauvegardes réactivées"));
  $("#sauv-heure").onchange = (event) =>
    event.target.value && save({ hour: event.target.value }, t`Chaque nuit à ${event.target.value}`);
  content
    .querySelectorAll("[data-source]")
    .forEach((element) =>
      element.addEventListener("click", () =>
        save(
          { sources: { [element.dataset.source]: element.getAttribute("aria-checked") !== "true" } },
          t("Contenu des sauvegardes changé"),
        ),
      ),
    );
  $("#sauv-disque").onchange = async (event) => {
    const name = event.target.value;
    if (
      !(await confirmAction({
        title: t`Sauvegarder sur ${name} ?`,
        text: t`Les prochaines sauvegardes iront sur ${name}. Celles déjà faites restent sur ${data.disk}. La phrase de passe ne change pas.`,
        confirmLabel: t("Changer"),
      }))
    ) {
      event.target.value = data.disk;
      return;
    }
    try {
      await api("/api/sauvegardes/activer", { method: "POST", json: { disk: name } });
      toast(t`Sauvegardes vers ${name}`);
    } catch (error) {
      toast(error.message, true);
    }
    pages.sauvegardes().catch(showError);
  };
  $("#sauv-phrase").onclick = async () => {
    try {
      const data = await api("/api/sauvegardes/phrase", { method: "POST" });
      await showPassphrase(data.phrase, false);
    } catch (error) {
      toast(error.message, true);
    }
  };
};

async function snapshotList() {
  const element = $("#sauv-copies");
  element.innerHTML = t`<h2>Retrouver un fichier</h2>${loadingHtml(2)}`;
  const { copies: snapshots } = await api("/api/sauvegardes/historique");
  element.innerHTML = t`<div class="entete-carte"><h2>Retrouver un fichier</h2></div>
    <p class="note" style="margin:8px 0 12px">${snapshots.length ? t("Choisis la date de la copie.") : t("Aucune sauvegarde pour l'instant.")}</p>
    <div class="sauv-liste">${snapshots.map((item) => `<button class="sauv-ligne" data-copie="${escapeHtml(item.id)}"><span><strong>${escapeHtml(formatWhen(item.date))}</strong> <span class="note">${escapeHtml(new Date(item.date).toLocaleString("fr-FR", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" }))}</span></span><span>›</span></button>`).join("")}</div>`;
  element.querySelectorAll("[data-copie]").forEach((element) =>
    element.addEventListener("click", () => {
      const item = snapshots.find((item) => item.id === element.dataset.copie);
      browseSnapshot(item, null).catch((entry) => toast(entry.message, true));
    }),
  );
}

const ROOT_NAMES = {
  "/srv/data": t("Fichiers"),
  "/opt/stacks": t("Réglages des outils"),
  "/etc": t("Réglages du serveur"),
};
async function browseSnapshot(item, path) {
  const element = $("#sauv-copies");
  const date = new Date(item.date).toLocaleString("fr-FR", {
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  });
  const root = path && item.paths.find((result) => path === result || path.startsWith(result + "/"));
  let entries;
  if (!path) entries = item.paths.map((result) => ({ name: ROOT_NAMES[result] || result, path: result, folder: true }));
  else {
    element.querySelector(".sauv-liste").innerHTML = '<div class="squelette"></div>';
    entries = (
      await api(`/api/sauvegardes/parcourir?id=${encodeURIComponent(item.id)}&chemin=${encodeURIComponent(path)}`)
    ).items;
  }
  const parts = [];
  if (root) {
    parts.push([ROOT_NAMES[root] || root, root]);
    let accumulator = root;
    for (const value of path.slice(root.length).split("/").filter(Boolean)) {
      accumulator += "/" + value;
      parts.push([value, accumulator]);
    }
  }
  element.innerHTML = t`<div class="entete-carte"><h2>Copie du ${escapeHtml(date)}</h2><button class="btn btn-petit" id="sauv-dates">Autre date</button></div>
    <div class="fil" style="margin:10px 0"><a href="#" data-aller="">Début</a>${parts.map(([item, value]) => `<span class="sep">›</span><a href="#" data-aller="${escapeHtml(value)}">${escapeHtml(item)}</a>`).join("")}</div>
    <div class="sauv-liste">${
      entries
        .map(
          (item) => `<div class="sauv-ligne">
        ${item.folder ? `<a href="#" data-aller="${escapeHtml(item.path)}"><strong>${escapeHtml(item.name)}</strong></a>` : `<span>${escapeHtml(item.name)} <span class="note">${formatBytes(item.size)}</span></span>`}
        ${path ? t`<button class="btn btn-petit" data-restaurer="${escapeHtml(item.path)}">Récupérer</button>` : ""}</div>`,
        )
        .join("") || t('<p class="note">Dossier vide.</p>')
    }</div>`;
  $("#sauv-dates").onclick = () => snapshotList().catch((entry) => toast(entry.message, true));
  element.querySelectorAll("[data-aller]").forEach((element) =>
    element.addEventListener("click", (event) => {
      event.preventDefault();
      browseSnapshot(item, element.dataset.aller || null).catch((entry) => toast(entry.message, true));
    }),
  );
  element.querySelectorAll("[data-restaurer]").forEach((element) =>
    element.addEventListener("click", async () => {
      const value = element.dataset.restaurer;
      const name = value.split("/").pop();
      if (
        !(await confirmAction({
          title: t`Récupérer « ${name} » ?`,
          text: t`Il revient tel qu'il était le ${date}, dans Mes dossiers › Restaurations. Rien n'est remplacé.`,
          confirmLabel: t("Récupérer"),
        }))
      )
        return;
      element.disabled = true;
      element.textContent = t("Récupération…");
      try {
        await api("/api/sauvegardes/restaurer", { method: "POST", json: { id: item.id, path: value } });
        toast(t`« ${name} » est dans Mes dossiers › Restaurations`);
        element.textContent = t("Récupéré");
      } catch (error) {
        toast(error.message, true);
        element.disabled = false;
        element.textContent = t("Récupérer");
      }
    }),
  );
}
