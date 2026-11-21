// Antivirus dans l'Établi : la proposition d'installation, puis le détail des analyses.
//
// Sommaire :
//   partagé : antivirusOfferHtml, bindAntivirus
//   interne : detailHtml, disksHtml, bindDisks
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { formatWhen } from "../core/format.js";
import { t } from "../core/i18n.js";
import { state } from "../core/state.js";
import { confirmAction, toast } from "./dialogs.js";
import { toolIcon } from "../pages/home.js";

export function antivirusOfferHtml() {
  const isSuper = state.admin && state.admin.rang === "super";
  return t`<article class="carte pad outil" id="offre-antivirus">
      <div class="haut"><div class="titre-outil">${toolIcon({ id: "antivirus", nom: t("Antivirus") })}<div><h2>Antivirus</h2><div class="role">Analyse les fichiers qui arrivent et les disques choisis (ClamAV)</div></div></div></div>
      <p class="note">Les dépôts des invités sont analysés dans la minute, les nouveaux fichiers du disque de données chaque heure, et les disques que tu coches chaque semaine, la nuit. Un fichier infecté est mis à l'écart (jamais supprimé) et tu reçois une alerte. Il utilise environ 1 Go de mémoire et télécharge environ 300 Mo à l'installation.</p>
      ${isSuper ? '<div class="actions"><button class="btn btn-p" type="button" id="installer-antivirus">' + t("Installer") + "</button></div>" : ""}
    </article>`;
}

/** Les disques : cochés pour l'analyse de la semaine, dernière analyse, « Analyser maintenant ». */
function disksHtml(disks) {
  if (!disks || !disks.length) return "";
  const busy = disks.some((disk) => disk.analyse && disk.analyse.etat === "en_cours");
  const lastScan = (scan) => {
    if (!scan) return t("Jamais analysé");
    if (scan.etat === "en_cours")
      return t`Analyse en cours depuis ${escapeHtml(formatWhen(new Date(scan.debut * 1000).toISOString()))}`;
    if (scan.etat === "erreur") return t`L'analyse n'a pas pu démarrer : ${escapeHtml(scan.erreur || "")}`;
    const found = scan.menaces
      ? t`${scan.menaces} fichier${scan.menaces > 1 ? "s" : ""} infecté${scan.menaces > 1 ? "s" : ""}`
      : t("rien trouvé");
    return t`Analysé ${escapeHtml(formatWhen(new Date(scan.fin * 1000).toISOString()))} : ${found}`;
  };
  return t`<h3 class="petit-titre">Analyse des disques</h3>
    <p class="note">Les disques cochés sont analysés en entier chaque semaine, la nuit, un à la fois et sans ralentir le serveur.</p>
    ${disks
      .map(
        (disk) => `<div class="ligne-reglage disque-antivirus">
      <span><strong>${escapeHtml(disk.nom)}</strong><br><span class="note">${lastScan(disk.analyse)}</span></span>
      <span class="actions">
        <span class="note">${t("Chaque semaine")}</span>
        <button class="interrupteur" type="button" role="switch" aria-label="${escapeHtml(t`Analyser ${disk.nom} chaque semaine`)}" aria-checked="${disk.chaque_semaine}" data-chaque-semaine="${escapeHtml(disk.chemin)}"><span></span></button>
        <button class="btn" type="button" data-analyser="${escapeHtml(disk.chemin)}" ${busy ? "disabled" : ""}>${t("Analyser maintenant")}</button>
      </span></div>`,
      )
      .join("")}`;
}

function bindDisks(detail, refresh) {
  detail.querySelectorAll("[data-chaque-semaine]").forEach((toggle) => {
    toggle.onclick = async () => {
      const enabled = toggle.getAttribute("aria-checked") !== "true";
      try {
        await api("/api/antivirus/disques", {
          method: "POST",
          json: { chemin: toggle.dataset.chaqueSemaine, chaque_semaine: enabled },
        });
        toggle.setAttribute("aria-checked", String(enabled));
        toast(enabled ? t("Analysé chaque semaine") : t("Plus analysé chaque semaine"));
      } catch (error) {
        toast(error.message, true);
      }
    };
  });
  detail.querySelectorAll("[data-analyser]").forEach((button) => {
    button.onclick = async () => {
      button.disabled = true;
      try {
        await api("/api/antivirus/analyser", { method: "POST", json: { chemin: button.dataset.analyser } });
        toast(t("Analyse lancée : elle peut durer plusieurs heures sur un gros disque."));
        refresh();
      } catch (error) {
        toast(error.message, true);
        button.disabled = false;
      }
    };
  });
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
          "Pas encore d'analyse : les nouveaux fichiers sont analysés chaque heure (le premier démarrage prend quelques minutes).",
        )
  } Les fichiers de plus de 100 Mo (vidéos) ne sont pas analysés.</p>${threats}
    ${disksHtml(summary.disques)}
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
      bindDisks(detail, refresh);
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
