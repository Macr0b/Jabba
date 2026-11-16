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

let backupFollower = null;

async function showPassphrase(passphrase, first) {
  await new Promise((resolve) => {
    dialogForm.innerHTML = `<h2>${first ? "La clé de tes sauvegardes" : "Phrase de passe des sauvegardes"}</h2>
      <p>Tes sauvegardes sont chiffrées avec cette phrase. <strong>Sans elle, personne ne peut les relire, toi non plus</strong>, par exemple si le serveur est perdu et qu'il faut tout récupérer sur un autre ordinateur.</p>
      <p>${first ? "Elle ne sera plus affichée d'elle-même : enregistre-la maintenant dans l'app Mots de passe et note-la sur papier, rangée ailleurs que le disque." : "Garde-la dans l'app Mots de passe et sur papier."}</p>
      <p class="code-secours">${escapeHtml(passphrase)}</p>
      <div class="actions"><button class="btn" type="button" id="copier-phrase">Copier</button><button class="btn btn-p" value="ok" type="submit">C'est noté</button></div>`;
    dialogElement.onclose = () => resolve();
    dialogElement.showModal();
    $("#copier-phrase").onclick = async () => {
      try {
        await navigator.clipboard.writeText(passphrase);
        toast("Phrase copiée");
      } catch (_) {
        toast("Copie impossible : sélectionne la phrase à la main.", true);
      }
    };
  });
}

function backupStateHtml(item) {
  if (item.en_cours)
    return `<span class="pastille p-alerte">En cours</span> <span class="note">depuis ${timeOf(item.depuis)}</span>`;
  const result = item.dernier;
  if (!result) return `<span class="pastille p-gris">Pas encore faite</span>`;
  if (!result.ok)
    return `<span class="pastille p-rouge">Échouée</span> <span class="note">${escapeHtml(timeAgo(result.fin))}</span><br><span style="color:var(--rouge)">${escapeHtml(result.erreur)}</span>`;
  return `<span class="pastille p-ok">Réussie</span> <span class="note">${escapeHtml(timeAgo(result.fin))} à ${timeOf(result.fin)}, en ${shortDuration(result.duree)}</span>${result.avert ? `<br><span class="note">${escapeHtml(result.avert)}</span>` : ""}`;
}

pages.sauvegardes = async () => {
  if (backupFollower) {
    clearTimeout(backupFollower);
    backupFollower = null;
  }
  content.innerHTML = `<div class="entete"><div><h1>Sauvegardes</h1></div></div>${loadingHtml()}`;
  const data = await api("/api/sauvegardes");
  const superAdmin = state.admin && state.admin.rang === "super";
  const diskPickerHtml = (selected) =>
    `<select id="sauv-disque">${data.disques.map((item) => `<option value="${escapeHtml(item.nom)}"${item.nom === selected ? " selected" : ""}>${escapeHtml(item.nom)}</option>`).join("")}</select>`;
  const what = `<p class="note" style="margin:8px 0">Chaque nuit, une copie chiffrée part sur le disque choisi : tes dossiers (sans les films ni les téléchargements, qui existent ailleurs), les réglages des outils avec leurs bases, et ceux du serveur. Seules les nouveautés prennent de la place. On garde les ${data.garder.jours} derniers jours, ${data.garder.semaines} semaines et ${data.garder.mois} mois.</p>`;
  if (!data.pret) {
    content.innerHTML = `<div class="entete"><div><h1>Sauvegardes</h1><p class="sous-titre">Pas encore en route.</p></div></div>
      <section class="carte pad"><h2>Mettre en route</h2>${what}
        <p class="note" style="margin:8px 0 14px">La copie est rangée dans un dossier caché du disque (<code>.sauvegardes-jabba</code>), à côté de ce qu'il contient déjà. Un disque en lecture seule n'est ouvert en écriture que le temps de la copie.</p>
        ${
          data.disques.length
            ? superAdmin
              ? `<label class="note" for="sauv-disque">Disque des sauvegardes</label>${diskPickerHtml(data.disques[0].nom)}
          <div class="actions" style="margin-top:14px"><button class="btn btn-p" id="sauv-activer">Mettre en route</button></div>`
              : '<p class="note">Seul un super admin peut les mettre en route.</p>'
            : '<div class="bandeau"><span>Aucun disque externe : branche celui qui recevra les sauvegardes, il apparaîtra ici.</span></div>'
        }
      </section>`;
    const button = $("#sauv-activer");
    if (button)
      button.onclick = async () => {
        button.disabled = true;
        button.textContent = "Mise en route…";
        try {
          const data = await api("/api/sauvegardes/activer", {
            method: "POST",
            json: { disque: $("#sauv-disque").value },
          });
          if (data.phrase) await showPassphrase(data.phrase, true);
          toast("Sauvegardes en route : la première peut partir tout de suite");
        } catch (error) {
          toast(error.message, true);
        }
        pages.sauvegardes().catch(showError);
      };
    return;
  }
  const result = data.derniere_reussie;
  content.innerHTML = `
    <div class="entete"><div><h1>Sauvegardes</h1><p class="sous-titre">${data.actif ? `Chaque nuit à ${escapeHtml(data.heure)}, vers ${escapeHtml(data.disque)}` : "En pause"}</p></div>
      <div class="actions"><button class="btn btn-p" id="sauv-lancer"${data.en_cours || !data.branche ? " disabled" : ""}>${data.en_cours ? "Sauvegarde en cours…" : "Sauvegarder maintenant"}</button></div></div>
    ${data.branche ? "" : `<div class="bandeau"><span>Le disque ${escapeHtml(data.disque)} n'est pas branché. ${data.actif ? "La sauvegarde partira toute seule dès qu'il reviendra." : ""}</span></div>`}
    <div class="grille g2">
      <section class="carte pad"><h2>Dernière sauvegarde</h2>
        <p style="margin:10px 0">${backupStateHtml(data)}</p>
        <dl class="cles">
          <dt>Dernière réussie</dt><dd>${result ? `${escapeHtml(dayOf(result.fin))} à ${timeOf(result.fin)}` : "Aucune"}</dd>
          ${
            result && result.taille != null
              ? `<dt>Contenu</dt><dd>${formatBytes(result.taille)} · ${(result.fichiers || 0).toLocaleString("fr-FR")} fichiers</dd>
          <dt>Nouveautés copiées</dt><dd>${formatBytes(result.ajoute)}${result.nouveaux != null ? ` <span class="note">(${result.nouveaux} nouveaux, ${result.modifies} modifiés)</span>` : ""}</dd>`
              : ""
          }
          <dt>Prochaine</dt><dd>${data.prochaine ? (data.prochaine * 1000 - Date.now() < 86400000 ? (+data.heure.slice(0, 2) < 12 ? "Cette nuit" : "Aujourd'hui") : escapeHtml(formatWhen(new Date(data.prochaine * 1000).toISOString()))) + ` à ${escapeHtml(data.heure)}` : "En pause"}</dd>
        </dl>
      </section>
      <section class="carte pad"><h2>Réglages</h2>${what}
        ${
          superAdmin
            ? `
        <label class="ligne-reglage"><span><strong>Sauvegarde chaque nuit</strong></span>
          <button class="interrupteur" role="switch" aria-checked="${data.actif}" id="sauv-actif" aria-label="Sauvegarde chaque nuit"><span></span></button></label>
        <label class="ligne-reglage"><span><strong>Heure</strong><br><span class="note">Une sauvegarde manquée (serveur éteint, disque absent) part dès que possible.</span></span>
          <input type="time" id="sauv-heure" value="${escapeHtml(data.heure)}" style="width:auto"></label>
        ${data.sources
          .map(
            (
              item,
            ) => `<label class="ligne-reglage"><span><strong>${escapeHtml(item.nom)}</strong><br><span class="note">${escapeHtml(item.detail)}</span></span>
          <button class="interrupteur" role="switch" aria-checked="${item.actif}" data-source="${escapeHtml(item.id)}" aria-label="${escapeHtml(item.nom)}"><span></span></button></label>`,
          )
          .join("")}
        <label class="ligne-reglage"><span><strong>Disque</strong></span><span style="min-width:140px">${diskPickerHtml(data.disque)}</span></label>
        <div class="actions" style="margin-top:12px"><button class="btn" id="sauv-phrase">Revoir la phrase de passe</button></div>`
            : `<p class="note">Seul un super admin peut changer ces réglages.</p>`
        }
      </section>
    </div>
    <section class="carte pad" id="sauv-copies" style="margin-top:16px"><div class="entete-carte"><h2>Retrouver un fichier</h2></div>
      <p class="note" style="margin:8px 0 12px">Choisis une date, puis le fichier ou le dossier à récupérer. Il revient dans <strong>Mes dossiers › Restaurations</strong> : rien n'est remplacé.</p>
      <div class="actions"><button class="btn" id="sauv-voir"${data.branche ? "" : " disabled"}>Voir les sauvegardes</button></div>
    </section>`;
  $("#sauv-lancer").onclick = async () => {
    try {
      await api("/api/sauvegardes/lancer", { method: "POST" });
      toast("Sauvegarde lancée");
    } catch (error) {
      toast(error.message, true);
    }
    pages.sauvegardes().catch(showError);
  };
  $("#sauv-voir").onclick = () => snapshotList().catch((entry) => toast(entry.message, true));
  if (data.en_cours)
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
    save({ actif: !data.actif }, data.actif ? "Sauvegardes en pause" : "Sauvegardes réactivées");
  $("#sauv-heure").onchange = (event) =>
    event.target.value && save({ heure: event.target.value }, `Chaque nuit à ${event.target.value}`);
  content
    .querySelectorAll("[data-source]")
    .forEach((element) =>
      element.addEventListener("click", () =>
        save(
          { sources: { [element.dataset.source]: element.getAttribute("aria-checked") !== "true" } },
          "Contenu des sauvegardes changé",
        ),
      ),
    );
  $("#sauv-disque").onchange = async (event) => {
    const name = event.target.value;
    if (
      !(await confirmAction({
        titre: `Sauvegarder sur ${name} ?`,
        texte: `Les prochaines sauvegardes iront sur ${name}. Celles déjà faites restent sur ${data.disque}. La phrase de passe ne change pas.`,
        valider: "Changer",
      }))
    ) {
      event.target.value = data.disque;
      return;
    }
    try {
      await api("/api/sauvegardes/activer", { method: "POST", json: { disque: name } });
      toast(`Sauvegardes vers ${name}`);
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
  element.innerHTML = `<h2>Retrouver un fichier</h2>${loadingHtml(2)}`;
  const { copies: snapshots } = await api("/api/sauvegardes/historique");
  element.innerHTML = `<div class="entete-carte"><h2>Retrouver un fichier</h2></div>
    <p class="note" style="margin:8px 0 12px">${snapshots.length ? "Choisis la date de la copie." : "Aucune sauvegarde pour l'instant."}</p>
    <div class="sauv-liste">${snapshots.map((item) => `<button class="sauv-ligne" data-copie="${escapeHtml(item.id)}"><span><strong>${escapeHtml(formatWhen(item.date))}</strong> <span class="note">${escapeHtml(new Date(item.date).toLocaleString("fr-FR", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" }))}</span></span><span>›</span></button>`).join("")}</div>`;
  element.querySelectorAll("[data-copie]").forEach((element) =>
    element.addEventListener("click", () => {
      const item = snapshots.find((item) => item.id === element.dataset.copie);
      browseSnapshot(item, null).catch((entry) => toast(entry.message, true));
    }),
  );
}

const ROOT_NAMES = { "/srv/data": "Fichiers", "/opt/stacks": "Réglages des outils", "/etc": "Réglages du serveur" };
async function browseSnapshot(item, path) {
  const element = $("#sauv-copies");
  const date = new Date(item.date).toLocaleString("fr-FR", {
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  });
  const root = path && item.chemins.find((result) => path === result || path.startsWith(result + "/"));
  let entries;
  if (!path)
    entries = item.chemins.map((result) => ({ nom: ROOT_NAMES[result] || result, chemin: result, dossier: true }));
  else {
    element.querySelector(".sauv-liste").innerHTML = '<div class="squelette"></div>';
    entries = (
      await api(`/api/sauvegardes/parcourir?id=${encodeURIComponent(item.id)}&chemin=${encodeURIComponent(path)}`)
    ).elements;
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
  element.innerHTML = `<div class="entete-carte"><h2>Copie du ${escapeHtml(date)}</h2><button class="btn btn-petit" id="sauv-dates">Autre date</button></div>
    <div class="fil" style="margin:10px 0"><a href="#" data-aller="">Début</a>${parts.map(([item, value]) => `<span class="sep">›</span><a href="#" data-aller="${escapeHtml(value)}">${escapeHtml(item)}</a>`).join("")}</div>
    <div class="sauv-liste">${
      entries
        .map(
          (item) => `<div class="sauv-ligne">
        ${item.dossier ? `<a href="#" data-aller="${escapeHtml(item.chemin)}"><strong>${escapeHtml(item.nom)}</strong></a>` : `<span>${escapeHtml(item.nom)} <span class="note">${formatBytes(item.taille)}</span></span>`}
        ${path ? `<button class="btn btn-petit" data-restaurer="${escapeHtml(item.chemin)}">Récupérer</button>` : ""}</div>`,
        )
        .join("") || '<p class="note">Dossier vide.</p>'
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
      const value = element.dataset.restaurer,
        name = value.split("/").pop();
      if (
        !(await confirmAction({
          titre: `Récupérer « ${name} » ?`,
          texte: `Il revient tel qu'il était le ${date}, dans Mes dossiers › Restaurations. Rien n'est remplacé.`,
          valider: "Récupérer",
        }))
      )
        return;
      element.disabled = true;
      element.textContent = "Récupération…";
      try {
        await api("/api/sauvegardes/restaurer", { method: "POST", json: { id: item.id, chemin: value } });
        toast(`« ${name} » est dans Mes dossiers › Restaurations`);
        element.textContent = "Récupéré";
      } catch (error) {
        toast(error.message, true);
        element.disabled = false;
        element.textContent = "Récupérer";
      }
    }),
  );
}
