// Carte « Santé des disques » de l'écran Disques (lecture SMART une fois par jour).
//
// Sommaire :
//   partagé : diskHealthCard
//   interne : STATE_CLASSES, usageText, diskLine, LEVEL_TEXT, maintenanceHtml, bindMaintenance
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { formatWhen } from "../core/format.js";
import { toast } from "./dialogs.js";
import { t } from "../core/i18n.js";

const STATE_CLASSES = { bon: "p-ok", a_surveiller: "p-alerte", critique: "p-rouge", inconnu: "p-gris" };

// Heures de fonctionnement cumulées depuis la fabrication (compteur du disque), pas depuis le démarrage.
function usageText(hours) {
  return t`${Math.round(hours / 24).toLocaleString("fr-FR")} jours de fonctionnement depuis sa fabrication`;
}

function diskLine(disk) {
  const facts = [
    disk.temperature != null ? `${disk.temperature} °C` : "",
    disk.heures != null ? usageText(disk.heures) : "",
    disk.usure != null ? t`usé à ${disk.usure} %` : "",
    disk.endormi ? t("endormi au dernier passage") : "",
  ].filter(Boolean);
  return `<div class="ligne-reglage">
      <span><strong>${escapeHtml(disk.nom)}</strong><br><span class="note">${escapeHtml([...disk.raisons, ...facts].join(" · ") || t("Pas encore lu"))}</span></span>
      <span class="pastille ${STATE_CLASSES[disk.etat] || "p-gris"}">${escapeHtml(disk.libelle)}</span>
    </div>`;
}

// Entretien : TRIM du SSD et fragmentation des disques à plateaux (jamais de défragmentation d'un SSD).
const LEVEL_TEXT = {
  faible: "faible",
  moyenne: "moyenne",
  forte: t("forte : à défragmenter"),
  inconnue: t("pas encore mesurée"),
};

async function maintenanceHtml() {
  const care = await api("/api/disques/entretien");
  const trim = care.trim;
  const lines = [];
  if (trim.ssd)
    lines.push(
      t`<div class="ligne-reglage"><span><strong>SSD : TRIM chaque semaine</strong><br><span class="note">${
        trim.actif
          ? t("Actif") +
            (trim.dernier ? t` · dernier passage : ${escapeHtml(trim.dernier)}` : "") +
            t(". Un SSD ne se défragmente jamais : le TRIM est son entretien.")
          : t("Pas actif : le SSD ralentit et s'use plus vite avec le temps.")
      }</span></span>${trim.actif ? t('<span class="pastille p-ok">Actif</span>') : t('<button class="btn btn-petit" type="button" id="activer-trim">Activer</button>')}</div>`,
    );
  care.disques.forEach((disk) => {
    lines.push(
      `<div class="ligne-reglage"><span><strong>${escapeHtml(disk.nom)} : fragmentation ${escapeHtml(LEVEL_TEXT[disk.niveau])}</strong><br><span class="note">${
        disk.score == null
          ? t("Mesurée une fois par mois.")
          : t`Score ${disk.score} sur 100 (au-delà de 55, ça vaut le coup). Mesurée une fois par mois.`
      }</span></span>${
        disk.niveau === "forte" || disk.niveau === "moyenne"
          ? t`<button class="btn btn-petit" type="button" data-defragmenter="${escapeHtml(disk.chemin)}"${care.en_cours ? " disabled" : ""}>Défragmenter</button>`
          : ""
      }</div>`,
    );
  });
  if (!lines.length) return "";
  return t`<h3 class="petit-titre">Entretien</h3>${care.en_cours ? t`<p class="note">En cours : ${escapeHtml(care.en_cours)}…</p>` : ""}${lines.join("")}
    ${care.disques.length ? t`<button class="lien" type="button" id="mesurer-fragmentation"${care.en_cours ? " disabled" : ""}>Mesurer la fragmentation maintenant</button>` : ""}`;
}

function bindMaintenance(refresh) {
  const trimButton = $("#activer-trim");
  if (trimButton)
    trimButton.onclick = async () => {
      try {
        await api("/api/disques/entretien/trim", { method: "POST" });
        toast(t("TRIM activé"));
      } catch (error) {
        toast(error.message, true);
      }
      refresh();
    };
  const measure = $("#mesurer-fragmentation");
  if (measure)
    measure.onclick = async () => {
      try {
        await api("/api/disques/entretien/mesurer", { method: "POST" });
        toast(t("Mesure lancée : quelques minutes, selon la taille du disque."));
      } catch (error) {
        toast(error.message, true);
      }
      refresh();
    };
  document.querySelectorAll("[data-defragmenter]").forEach((button) => {
    button.onclick = async () => {
      try {
        await api("/api/disques/entretien/defragmenter", {
          method: "POST",
          json: { chemin: button.dataset.defragmenter },
        });
        toast(t("Défragmentation lancée : elle peut durer des heures. Le disque reste utilisable."));
      } catch (error) {
        toast(error.message, true);
      }
      refresh();
    };
  });
}

export async function diskHealthCard() {
  const element = $("#sante-disques");
  if (!element) return;
  const health = await api("/api/disques/sante");
  if (!health.installe) {
    element.innerHTML = t`<h2>Santé des disques</h2>
      <p class="note" style="margin:6px 0 12px">Un disque qui va lâcher prévient souvent : secteurs abîmés, chaleur, usure. Jabba peut les lire chaque jour (sans réveiller un disque endormi) et t'alerter sur l'iPhone. Il faut pour cela installer l'outil smartmontools sur le serveur.</p>
      <button class="btn" type="button" id="activer-sante">Activer la surveillance</button>
      <div id="entretien-disques">${await maintenanceHtml().catch(() => "")}</div>`;
    bindMaintenance(() => diskHealthCard().catch((error) => toast(error.message, true)));
    $("#activer-sante").onclick = async () => {
      try {
        toast(t("Installation en cours…"));
        await api("/api/disques/sante/activer", { method: "POST" });
        toast(t("Surveillance activée"));
      } catch (error) {
        toast(error.message, true);
      }
      diskHealthCard().catch((error) => toast(error.message, true));
    };
    return;
  }
  element.innerHTML = t`<div class="entete-carte"><h2>Santé des disques</h2>
      <button class="btn btn-petit" type="button" id="verifier-sante">Vérifier maintenant</button></div>
    <p class="note" style="margin:6px 0 10px">${health.lu ? t("Dernière lecture : ") + escapeHtml(formatWhen(new Date(health.lu * 1000).toISOString())) + t(". Lue chaque jour ; une alerte part au premier signe de fatigue.") : t("Pas encore lue.")}</p>
    ${health.disques.map(diskLine).join("") || t('<p class="note">Aucun disque lisible.</p>')}
    <div id="entretien-disques">${await maintenanceHtml().catch(() => "")}</div>`;
  bindMaintenance(() => diskHealthCard().catch((error) => toast(error.message, true)));
  $("#verifier-sante").onclick = async (event) => {
    event.target.disabled = true;
    try {
      await api("/api/disques/sante/verifier", { method: "POST" });
    } catch (error) {
      toast(error.message, true);
    }
    diskHealthCard().catch((error) => toast(error.message, true));
  };
}
