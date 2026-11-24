// « Déménager vers un nouvel ordinateur », dans Système (super admin) : copie tout sur un disque,
// puis éteint l'ancien serveur ; le script d'installation du nouveau reprend tout (services/server_move.py).
// Pas de fenêtre : le choix du disque et la confirmation restent dans la carte.
//
// Sommaire :
//   partagé : serverMoveCard
//   interne : render, diskOptions, watchCopy, STEPS_AFTER
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { toast } from "./dialogs.js";

const STEPS_AFTER = [
  t("Mets le disque dans le nouvel ordinateur (ou branche-le dessus)."),
  t("Installe Debian avec le même nom de serveur et le même compte (guide, étapes 1 à 5)."),
  t("Lance le script d'installation : il trouve le déménagement et te propose de le reprendre."),
];

function diskOptions(disks) {
  return disks
    .map(
      (disk) =>
        `<option value="${escapeHtml(disk.path)}">${escapeHtml(disk.name)} — ${
          disk.files ? t("copie complète") : t("à déplacer dans le nouvel ordinateur")
        }</option>`,
    )
    .join("");
}

let watching = false;

async function watchCopy(card) {
  if (watching) return;
  watching = true;
  try {
    for (;;) {
      await new Promise((done) => setTimeout(done, 5000));
      const data = await api("/api/demenagement");
      if (data.state !== "en cours" || !document.body.contains(card)) {
        render(card, data);
        return;
      }
    }
  } finally {
    watching = false;
  }
}

function render(card, data) {
  const head = t`<h2>Déménager vers un nouvel ordinateur</h2>`;
  if (data.state === "en cours") {
    card.innerHTML = t`${head}<p class="note">Copie en cours. Les outils sont arrêtés pour que rien ne change pendant la copie. Selon la taille des fichiers, de quelques minutes à quelques heures : tu peux fermer cette page.</p><div class="squelette" style="margin-top:10px"></div>`;
    watchCopy(card);
    return;
  }
  if (data.state === "pret") {
    card.innerHTML = t`${head}<p><strong>La copie est prête.</strong> Les outils restent arrêtés sur ce serveur.</p>
      <ol class="note">${STEPS_AFTER.map((step) => `<li>${escapeHtml(step)}</li>`).join("")}</ol>
      <p class="note">Avant d'installer le nouveau, éteins celui-ci : il libère ses noms dans Tailscale, et le nouveau reprend les mêmes adresses. Tu ne pourras plus l'ouvrir à distance.</p>
      <div class="actions"><button class="btn" type="button" data-annuler>Annuler le déménagement</button>
      <button class="btn btn-danger" type="button" data-eteindre>Éteindre l'ancien serveur</button></div>`;
    card.querySelector("[data-eteindre]").onclick = async (event) => {
      const button = event.currentTarget;
      if (!button.dataset.sur) {
        button.dataset.sur = "1";
        button.textContent = t("Sûr ? Éteindre maintenant");
        return;
      }
      button.disabled = true;
      await api("/api/demenagement/terminer", { method: "POST" });
      card.innerHTML = t`${head}<p>L'ancien serveur s'éteint dans 30 secondes. Tu peux installer le nouveau.</p>`;
    };
    card.querySelector("[data-annuler]").onclick = async () => {
      render(card, await api("/api/demenagement/annuler", { method: "POST" }));
      toast(t("Déménagement annulé : les outils repartent"));
    };
    return;
  }
  const failed =
    data.state === "rate"
      ? t`<p class="erreur">La copie n'a pas abouti : ${escapeHtml(data.error)} Les outils sont repartis.</p>`
      : "";
  card.innerHTML = t`${head}${failed}<p class="note">Pour passer sur un autre ordinateur sans rien perdre : comptes, outils, réglages de Jabba, Nextcloud et ses fichiers, Jellyfin… Jabba copie tout sur un disque, puis le script d'installation du nouvel ordinateur remet tout en place.</p>
    ${
      data.disks.length
        ? t`<label class="champ"><span>Copier sur</span><select data-disque>${diskOptions(data.disks)}</select></label>
      <p class="note">Le plus simple : le disque de données, que tu mets dans le nouvel ordinateur (seuls les réglages y sont ajoutés). Sinon, un disque USB qui a la place pour tout.</p>
      <div class="actions">${data.state === "rate" ? t`<button class="btn" type="button" data-annuler>Effacer la copie ratée</button>` : ""}<button class="btn btn-p" type="button" data-lancer>Préparer le déménagement</button></div>`
        : t`<p class="note">Branche un disque USB pour y copier le serveur.</p>`
    }`;
  const start = card.querySelector("[data-lancer]");
  if (start)
    start.onclick = async (event) => {
      const button = event.currentTarget;
      if (!button.dataset.sur) {
        button.dataset.sur = "1";
        button.textContent = t("Sûr ? Les outils s'arrêtent");
        return;
      }
      button.disabled = true;
      render(
        card,
        await api("/api/demenagement/lancer", {
          method: "POST",
          json: { path: card.querySelector("[data-disque]").value },
        }),
      );
    };
  const cancel = card.querySelector("[data-annuler]");
  if (cancel) cancel.onclick = async () => render(card, await api("/api/demenagement/annuler", { method: "POST" }));
}

export async function serverMoveCard() {
  const card = $("#demenagement");
  if (!card) return;
  render(card, await api("/api/demenagement"));
}
