// Antivirus dans l'Établi : la proposition d'installation, puis le détail des analyses.
//
// Sommaire :
//   partagé : antivirusOfferHtml, bindAntivirus
//   interne : detailHtml
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { formatWhen } from "../core/format.js";
import { t } from "../core/i18n.js";
import { state } from "../core/state.js";
import { confirmAction, toast } from "./dialogs.js";
import { toolIcon } from "../pages/home.js";

export function antivirusOfferHtml() {
  const isSuper = state.admin && state.admin.rang === "super";
  return t`<h2 class="titre-section">À installer</h2>
    <div class="grille g2"><article class="carte pad outil" id="offre-antivirus">
      <div class="haut"><div class="titre-outil">${toolIcon({ nom: t("Antivirus") })}<div><h2>Antivirus</h2><div class="role">Analyse les fichiers déposés par les invités (ClamAV)</div></div></div></div>
      <p class="note">Chaque fichier déposé sur la page invités est analysé dans la minute. Un fichier infecté est mis à l'écart (jamais supprimé) et tu reçois une alerte. Il utilise environ 1 Go de mémoire et télécharge environ 300 Mo à l'installation.</p>
      ${isSuper ? '<div class="actions"><button class="btn btn-p" type="button" id="installer-antivirus">' + t("Installer") + "</button></div>" : ""}
    </article></div>`;
}

function detailHtml(summary) {
  const threats = summary.menaces.length
    ? t`<p class="note" style="color:var(--rouge)"><strong>${summary.menaces.length} fichier${summary.menaces.length > 1 ? "s" : ""} mis à l'écart :</strong></p><ul class="liste-inactifs">${summary.menaces
        .map(
          (item) =>
            t`<li>${escapeHtml(item.fichier)} <span class="note">· ${escapeHtml(item.menace)} · ${escapeHtml(formatWhen(new Date(item.date * 1000).toISOString()))}</span></li>`,
        )
        .join("")}</ul><p class="note">Ils sont dans le dossier caché .quarantaine-antivirus du disque de données.</p>`
    : t`<p class="note">Aucune menace trouvée.</p>`;
  return t`<p class="note">${
    summary.derniere_analyse
      ? t`Dernière analyse : ${escapeHtml(formatWhen(new Date(summary.derniere_analyse * 1000).toISOString()))} · ${summary.analyses} fichier${summary.analyses > 1 ? "s" : ""} analysé${summary.analyses > 1 ? "s" : ""} en tout.`
      : t(
          "Pas encore d'analyse : elle part dès qu'un invité dépose un fichier (le premier démarrage prend quelques minutes).",
        )
  } Les fichiers de plus de 100 Mo (vidéos) ne sont pas analysés.</p>${threats}
    ${state.admin && state.admin.rang === "super" ? t`<button class="lien" type="button" id="retirer-antivirus">Retirer l'antivirus</button>` : ""}`;
}

export function bindAntivirus(refresh) {
  const install = $("#installer-antivirus");
  if (install)
    install.onclick = async () => {
      install.disabled = true;
      try {
        await api("/api/antivirus/installer", { method: "POST" });
        toast(t("Installation lancée : quelques minutes. L'antivirus apparaîtra dans l'Établi."));
      } catch (error) {
        toast(error.message, true);
        install.disabled = false;
      }
    };
  const detail = $("#antivirus-detail");
  if (!detail) return;
  api("/api/antivirus")
    .then((summary) => {
      detail.innerHTML = detailHtml(summary);
      const remove = $("#retirer-antivirus");
      if (remove)
        remove.onclick = async () => {
          const ok = await confirmAction({
            titre: t("Retirer l'antivirus ?"),
            texte: t("Les dépôts des invités ne seront plus analysés. Les fichiers mis à l'écart restent où ils sont."),
            valider: t("Retirer"),
            danger: true,
          });
          if (!ok) return;
          try {
            await api("/api/antivirus/retirer", { method: "POST" });
            toast(t("Antivirus retiré"));
          } catch (error) {
            toast(error.message, true);
          }
          refresh();
        };
    })
    .catch(() => {});
}
