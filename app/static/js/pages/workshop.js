// Écran Mémoire d'atelier : les machines, puis le carnet d'une machine (réglages, rappels, journal).
// Outil de l'appli, à activer dans l'Établi. Les données vivent sur le serveur (services/workshop.py).
//
// Sommaire :
//   partagé : pages.atelier (écran : liste, ou une machine avec ?machine=…)
//   interne : listHtml, machineCardHtml, bindList, showMachine, remindersHtml, journalHtml, entryHtml, bindMachine
import { confirmAction, toast } from "../components/dialogs.js";
import { pickFolder } from "../components/folder_picker.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatDay } from "../core/format.js";
import { t } from "../core/i18n.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { loadingHtml } from "../core/tasks.js";

const TYPE_LABELS = {
  intervention: t("Intervention"),
  panne: t("Panne"),
  entretien: t("Entretien"),
  reglage: t("Réglage"),
  note: t("Note"),
};
const TYPE_BADGES = {
  panne: "p-rouge",
  entretien: "p-ok",
  intervention: "p-alerte",
  reglage: "p-gris",
  note: "p-gris",
};

const day = formatDay;
const today = () => new Date().toISOString().slice(0, 10);

pages.atelier = async (params) => {
  content.innerHTML = t`<div class="entete"><div><h1>Mémoire d'atelier</h1></div></div>${loadingHtml()}`;
  if (params.get("machine")) return showMachine(params.get("machine"));
  const data = await api("/api/atelier");
  content.innerHTML = listHtml(data.machines);
  bindList();
};

// ---------------------------------------------------------------- la liste des machines
function machineCardHtml(machine) {
  const last = machine.derniere
    ? t`Dernière note : ${escapeHtml(day(machine.derniere.date))} — ${escapeHtml(machine.derniere.titre)}`
    : t("Rien de noté pour l'instant");
  const details = [machine.modele, machine.emplacement].filter(Boolean).map(escapeHtml).join(" · ");
  return `<a class="carte pad machine-atelier" href="#/atelier?machine=${encodeURIComponent(machine.id)}">
      <div class="haut"><div><h2>${escapeHtml(machine.nom)}</h2>${details ? `<div class="role">${details}</div>` : ""}</div>
      ${machine.rappels_dus ? t`<span class="pastille p-alerte">${machine.rappels_dus} entretien${machine.rappels_dus > 1 ? "s" : ""} à faire</span>` : ""}</div>
      <p class="note">${last}</p>
    </a>`;
}

function listHtml(machines) {
  return t`<div class="entete"><div><h1>Mémoire d'atelier</h1><p class="sous-titre">Le carnet de chaque machine : interventions, pannes, réglages et rappels d'entretien.</p></div>
      <div class="actions"><button class="btn btn-p" type="button" id="nouvelle-machine">Ajouter une machine</button></div></div>
    <form class="carte pad formulaire-atelier" id="form-machine" hidden>
      <label class="champ"><span>Nom</span><input name="nom" required maxlength="80" placeholder="Laser Trotec"></label>
      <label class="champ"><span>Modèle</span><input name="modele" maxlength="120" placeholder="Speedy 360"></label>
      <label class="champ"><span>Emplacement</span><input name="emplacement" maxlength="80" placeholder="Fablab, atelier bois…"></label>
      <div class="actions"><button class="btn btn-p" type="submit">Ajouter</button><button class="btn" type="button" id="annuler-machine">Annuler</button></div>
    </form>
    ${
      machines.length
        ? `<div class="grille g2">${machines.map(machineCardHtml).join("")}</div>`
        : t`<p class="note">Aucune machine pour l'instant : ajoute la première.</p>`
    }`;
}

function bindList() {
  const form = $("#form-machine");
  $("#nouvelle-machine").onclick = () => {
    form.hidden = false;
    form.elements.nom.focus();
  };
  $("#annuler-machine").onclick = () => (form.hidden = true);
  form.onsubmit = async (event) => {
    event.preventDefault();
    const fields = Object.fromEntries(new FormData(form));
    try {
      const machine = await api("/api/atelier/machines", { method: "POST", json: fields });
      location.hash = "#/atelier?machine=" + encodeURIComponent(machine.id);
    } catch (error) {
      toast(error.message, true);
    }
  };
}

// ---------------------------------------------------------------- une machine
function remindersHtml(reminders) {
  const rows = reminders
    .map(
      (reminder) => `<li class="ligne-reglage">
      <span><strong>${escapeHtml(reminder.titre)}</strong><br><span class="note">${t`Tous les ${reminder.tous_les_jours} jours · prochain : ${escapeHtml(day(reminder.echeance))}`}</span></span>
      <span class="actions">${reminder.du ? t`<span class="pastille p-alerte">À faire</span>` : t`<span class="pastille p-ok">À jour</span>`}
        <button class="btn btn-petit" type="button" data-fait="${escapeHtml(reminder.id)}">${t("Fait aujourd'hui")}</button>
        <button class="btn btn-petit" type="button" data-retirer-rappel="${escapeHtml(reminder.id)}" aria-label="${escapeHtml(t`Retirer le rappel ${reminder.titre}`)}">×</button></span></li>`,
    )
    .join("");
  return t`<section class="carte pad"><h2>Rappels d'entretien</h2>
      ${rows ? `<ul class="liste-atelier">${rows}</ul>` : t`<p class="note">Aucun rappel. Exemple : « Nettoyer la lentille », tous les 30 jours.</p>`}
      <form class="formulaire-ligne" id="form-rappel">
        <label class="champ"><span>Entretien</span><input name="titre" required maxlength="200" placeholder="Nettoyer la lentille"></label>
        <label class="champ"><span>Tous les (jours)</span><input name="tous_les_jours" type="number" min="1" max="3650" value="30" required></label>
        <button class="btn" type="submit">Ajouter le rappel</button>
      </form></section>`;
}

function entryHtml(entry) {
  return `<li class="entree-atelier">
      <div class="haut"><span><span class="pastille ${TYPE_BADGES[entry.type] || "p-gris"}">${escapeHtml(TYPE_LABELS[entry.type] || entry.type)}</span>
        <strong>${escapeHtml(entry.titre)}</strong></span>
        <span class="note">${escapeHtml(day(entry.date))}${entry.par ? " · " + escapeHtml(entry.par) : ""}
        <button class="lien" type="button" data-retirer-entree="${escapeHtml(entry.id)}">${t("Retirer")}</button></span></div>
      ${entry.texte ? `<p class="texte-atelier">${escapeHtml(entry.texte)}</p>` : ""}
      ${entry.pieces ? t`<p class="note">Pièces : ${escapeHtml(entry.pieces)}</p>` : ""}
    </li>`;
}

function journalHtml(machine) {
  const options = Object.entries(TYPE_LABELS)
    .map(([value, label]) => `<option value="${value}">${escapeHtml(label)}</option>`)
    .join("");
  return t`<section class="carte pad"><h2>Journal</h2>
      <form class="formulaire-atelier" id="form-entree">
        <label class="champ"><span>Type</span><select name="type">${options}</select></label>
        <label class="champ"><span>Date</span><input name="date" type="date" value="${today()}" required></label>
        <label class="champ large"><span>Ce qui s'est passé</span><input name="titre" required maxlength="200" placeholder="Courroie de l'axe X changée"></label>
        <label class="champ large"><span>Détails</span><textarea name="texte" rows="3" maxlength="5000"></textarea></label>
        <label class="champ large"><span>Pièces changées</span><input name="pieces" maxlength="1000" placeholder="Courroie HTD 3M, réf. …"></label>
        <div class="actions"><button class="btn btn-p" type="submit">Noter</button></div>
      </form>
      ${
        machine.journal.length
          ? `<ul class="liste-atelier">${machine.journal.map(entryHtml).join("")}</ul>`
          : t`<p class="note">Rien de noté pour l'instant.</p>`
      }</section>`;
}

async function showMachine(machineId) {
  const machine = await api("/api/atelier/machines/" + encodeURIComponent(machineId));
  const details = [machine.modele, machine.emplacement].filter(Boolean).map(escapeHtml).join(" · ");
  content.innerHTML = t`<p><a class="lien" href="#/atelier">← Toutes les machines</a></p>
    <div class="entete"><div><h1>${escapeHtml(machine.nom)}</h1>${details ? `<p class="sous-titre">${details}</p>` : ""}</div>
      <div class="actions">
        ${machine.dossier ? t`<a class="btn" href="#/fichiers?chemin=${encodeURIComponent(machine.dossier)}">Manuels et photos</a>` : ""}
        <button class="btn" type="button" id="choisir-dossier">${machine.dossier ? t("Changer de dossier") : t("Choisir le dossier des manuels")}</button>
        <button class="btn" type="button" id="retirer-machine">Retirer la machine</button>
      </div></div>
    ${remindersHtml(machine.rappels)}
    <section class="carte pad"><h2>Réglages</h2>
      <textarea id="reglages" rows="6" maxlength="20000" aria-label="Réglages" placeholder="Puissance et vitesse par matériau, origines, astuces…">${escapeHtml(machine.reglages)}</textarea>
      <div class="actions"><button class="btn" type="button" id="enregistrer-reglages">Enregistrer les réglages</button></div></section>
    ${journalHtml(machine)}`;
  bindMachine(machine);
}

function bindMachine(machine) {
  const base = "/api/atelier/machines/" + encodeURIComponent(machine.id);
  const refresh = () => showMachine(machine.id).catch(showError);
  const send = async (url, options, message) => {
    try {
      await api(url, options);
      if (message) toast(message);
      refresh();
    } catch (error) {
      toast(error.message, true);
    }
  };
  $("#form-entree").onsubmit = (event) => {
    event.preventDefault();
    send(base + "/journal", { method: "POST", json: Object.fromEntries(new FormData(event.target)) }, t("Noté"));
  };
  $("#form-rappel").onsubmit = (event) => {
    event.preventDefault();
    const fields = Object.fromEntries(new FormData(event.target));
    send(base + "/rappels", { method: "POST", json: fields }, t("Rappel ajouté"));
  };
  $("#enregistrer-reglages").onclick = () =>
    send(base, { method: "PATCH", json: { reglages: $("#reglages").value } }, t("Réglages enregistrés"));
  $("#choisir-dossier").onclick = async () => {
    const folder = await pickFolder({
      title: t`Dossier des manuels et photos de ${machine.nom}`,
      start: machine.dossier || "/",
      confirmLabel: t("Choisir ce dossier"),
    });
    if (folder) send(base, { method: "PATCH", json: { dossier: folder } }, t("Dossier choisi"));
  };
  $("#retirer-machine").onclick = async () => {
    const ok = await confirmAction({
      titre: t`Retirer ${machine.nom} ?`,
      texte: t("Son journal, ses réglages et ses rappels seront effacés. Les fichiers de son dossier restent."),
      valider: t("Retirer"),
      danger: true,
    });
    if (!ok) return;
    try {
      await api(base, { method: "DELETE" });
      location.hash = "#/atelier";
    } catch (error) {
      toast(error.message, true);
    }
  };
  content.querySelectorAll("[data-fait]").forEach((button) => {
    button.onclick = () =>
      send(
        `/api/atelier/rappels/${encodeURIComponent(button.dataset.fait)}/fait`,
        { method: "POST" },
        t("Noté au journal"),
      );
  });
  content.querySelectorAll("[data-retirer-rappel]").forEach((button) => {
    button.onclick = () =>
      send(`/api/atelier/rappels/${encodeURIComponent(button.dataset.retirerRappel)}`, { method: "DELETE" });
  });
  content.querySelectorAll("[data-retirer-entree]").forEach((button) => {
    button.onclick = async () => {
      const ok = await confirmAction({ titre: t("Retirer cette note ?"), valider: t("Retirer"), danger: true });
      if (ok) send(`${base}/journal/${encodeURIComponent(button.dataset.retirerEntree)}`, { method: "DELETE" });
    };
  });
}
