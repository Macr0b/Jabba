// Carte « Santé des disques » de l'écran Disques (lecture SMART une fois par jour).
//
// Sommaire :
//   partagé : diskHealthCard
//   interne : STATE_CLASSES, diskLine
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { formatWhen } from "../core/format.js";
import { toast } from "./dialogs.js";

const STATE_CLASSES = { bon: "p-ok", a_surveiller: "p-alerte", critique: "p-rouge", inconnu: "p-gris" };

function diskLine(disk) {
  const facts = [
    disk.temperature != null ? `${disk.temperature} °C` : "",
    disk.heures != null ? `${Math.round(disk.heures / 24)} jours allumé` : "",
    disk.usure != null ? `usé à ${disk.usure} %` : "",
    disk.endormi ? "endormi au dernier passage" : "",
  ].filter(Boolean);
  return `<div class="ligne-reglage">
      <span><strong>${escapeHtml(disk.nom)}</strong><br><span class="note">${escapeHtml([...disk.raisons, ...facts].join(" · ") || "Pas encore lu")}</span></span>
      <span class="pastille ${STATE_CLASSES[disk.etat] || "p-gris"}">${escapeHtml(disk.libelle)}</span>
    </div>`;
}

export async function diskHealthCard() {
  const element = $("#sante-disques");
  if (!element) return;
  const health = await api("/api/disques/sante");
  if (!health.installe) {
    element.innerHTML = `<h2>Santé des disques</h2>
      <p class="note" style="margin:6px 0 12px">Un disque qui va lâcher prévient souvent : secteurs abîmés, chaleur, usure. Jabba peut les lire chaque jour (sans réveiller un disque endormi) et t'alerter sur l'iPhone. Il faut pour cela installer l'outil smartmontools sur le serveur.</p>
      <button class="btn" type="button" id="activer-sante">Activer la surveillance</button>`;
    $("#activer-sante").onclick = async () => {
      try {
        toast("Installation en cours…");
        await api("/api/disques/sante/activer", { method: "POST" });
        toast("Surveillance activée");
      } catch (error) {
        toast(error.message, true);
      }
      diskHealthCard().catch((error) => toast(error.message, true));
    };
    return;
  }
  element.innerHTML = `<div class="entete-carte"><h2>Santé des disques</h2>
      <button class="btn btn-petit" type="button" id="verifier-sante">Vérifier maintenant</button></div>
    <p class="note" style="margin:6px 0 10px">${health.lu ? "Dernière lecture : " + escapeHtml(formatWhen(new Date(health.lu * 1000).toISOString())) + ". Lue chaque jour ; une alerte part au premier signe de fatigue." : "Pas encore lue."}</p>
    ${health.disques.map(diskLine).join("") || '<p class="note">Aucun disque lisible.</p>'}`;
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
