// « Si le serveur ne répond plus », dans Système (super admin) : un service extérieur (healthchecks.io)
// reçoit un signal de vie toutes les 5 minutes et prévient quand il n'en reçoit plus (services/heartbeat.py).
// Les alertes de Jabba partent du serveur : s'il est éteint ou figé, seul ce service peut le dire.
//
// Sommaire :
//   partagé : heartbeatCard
//   interne : render
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { timeAgo } from "../core/format.js";
import { t } from "../core/i18n.js";
import { toast } from "./dialogs.js";

function render(card, data) {
  const head = t`<h2>Si le serveur ne répond plus</h2>`;
  if (data.pret) {
    card.innerHTML = t`${head}<p><span class="pastille ${data.ok ? "p-ok" : "p-alerte"}">${data.ok ? t("En place") : t("Dernier signal raté")}</span>
      <span class="note">Dernier signal reçu : ${escapeHtml(timeAgo(data.dernier))}.</span></p>
      <p class="note">Jabba envoie un signal de vie toutes les 5 minutes à healthchecks.io. S'il se tait (serveur éteint, figé, sans courant ou sans Internet), c'est healthchecks.io qui te prévient.</p>
      <div class="actions"><button class="lien" type="button" data-retirer>Retirer</button></div>`;
    card.querySelector("[data-retirer]").onclick = async (event) => {
      const button = event.currentTarget;
      if (!button.dataset.sur) {
        button.dataset.sur = "1";
        button.textContent = t("Sûr ? Mets d'abord la vérification en pause sur healthchecks.io");
        return;
      }
      render(card, await api("/api/surveillance/oublier", { method: "POST" }));
    };
    return;
  }
  card.innerHTML = t`${head}<p class="note">Les alertes de Jabba partent du serveur : s'il est éteint ou figé, il ne peut rien te dire. Un service extérieur gratuit le remarque à sa place.</p>
    <ol class="note">
      <li>Crée un compte sur healthchecks.io (gratuit), puis <strong>Add Check</strong>.</li>
      <li>Réglages : <strong>Period 5 minutes</strong>, <strong>Grace 15 minutes</strong> (le temps d'un redémarrage), et choisis comment être prévenu (mail, appli).</li>
      <li>Copie son <strong>Ping URL</strong> et colle-la ici.</li>
    </ol>
    <p><a class="btn" href="${escapeHtml(data.page)}" target="_blank" rel="noopener">Ouvrir healthchecks.io</a></p>
    <label class="champ"><span>Ping URL</span><input type="text" data-url autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="https://hc-ping.com/…"></label>
    <div class="actions"><button class="btn btn-p" type="button" data-enregistrer>Enregistrer et envoyer un premier signal</button></div>`;
  card.querySelector("[data-enregistrer]").onclick = async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      render(
        card,
        await api("/api/surveillance", { method: "POST", json: { url: card.querySelector("[data-url]").value } }),
      );
      toast(t("Premier signal reçu par healthchecks.io."));
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
    }
  };
}

export async function heartbeatCard() {
  const card = $("#surveillance");
  if (!card) return;
  render(card, await api("/api/surveillance"));
}
