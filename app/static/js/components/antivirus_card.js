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
  const isSuper = state.admin && state.admin.rank === "super";
  return t`<article class="carte pad outil" id="offre-antivirus">
      <div class="haut"><div class="titre-outil">${toolIcon({ id: "antivirus", name: t("Antivirus") })}<div><h2>Antivirus</h2><div class="role">Analyse les fichiers qui arrivent et les disques choisis (ClamAV)</div></div></div></div>
      <p class="note">Les dépôts des invités sont analysés dans la minute, les nouveaux fichiers du disque de données chaque heure, et les disques que tu coches chaque semaine, la nuit. Un fichier infecté est mis à l'écart (jamais supprimé) et tu reçois une alerte. Il utilise environ 1 Go de mémoire et télécharge environ 300 Mo à l'installation.</p>
      ${isSuper ? '<div class="actions"><button class="btn btn-p" type="button" id="installer-antivirus">' + t("Installer") + "</button></div>" : ""}
    </article>`;
}

/** Les disques : cochés pour l'analyse de la semaine, dernière analyse, « Analyser maintenant ». */
function disksHtml(disks) {
  if (!disks || !disks.length) return "";
  const busy = disks.some((disk) => disk.scan && disk.scan.state === "en_cours");
  const lastScan = (scan) => {
    if (!scan) return t("Jamais analysé");
    if (scan.state === "en_cours")
      return t`Analyse en cours depuis ${escapeHtml(formatWhen(new Date(scan.start * 1000).toISOString()))}`;
    if (scan.state === "erreur") return t`L'analyse n'a pas pu démarrer : ${escapeHtml(scan.error || "")}`;
    const found = scan.threats
      ? t`${scan.threats} fichier${scan.threats > 1 ? "s" : ""} infecté${scan.threats > 1 ? "s" : ""}`
      : t("rien trouvé");
    return t`Analysé ${escapeHtml(formatWhen(new Date(scan.end * 1000).toISOString()))} : ${found}`;
  };
  return t`<h3 class="petit-titre">Analyse des disques</h3>
    <p class="note">Les disques cochés sont analysés en entier chaque semaine, la nuit, un à la fois et sans ralentir le serveur.</p>
    ${disks
      .map(
        (disk) => `<div class="ligne-reglage disque-antivirus">
      <span><strong>${escapeHtml(disk.name)}</strong><br><span class="note">${lastScan(disk.scan)}</span></span>
      <span class="actions">
        <span class="note">${t("Chaque semaine")}</span>
        <button class="interrupteur" type="button" role="switch" aria-label="${escapeHtml(t`Analyser ${disk.name} chaque semaine`)}" aria-checked="${disk.weekly}" data-chaque-semaine="${escapeHtml(disk.path)}"><span></span></button>
        <button class="btn" type="button" data-analyser="${escapeHtml(disk.path)}" ${busy ? "disabled" : ""}>${t("Analyser maintenant")}</button>
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
          json: { path: toggle.dataset.chaqueSemaine, weekly: enabled },
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
        await api("/api/antivirus/analyser", { method: "POST", json: { path: button.dataset.analyser } });
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
  const threats = summary.threats.length
    ? t`<p class="note" style="color:var(--rouge)"><strong>${summary.threats.length} fichier${summary.threats.length > 1 ? "s" : ""} mis à l'écart :</strong></p><ul class="liste-inactifs">${summary.threats
        .map(
          (item) =>
            t`<li>${escapeHtml(item.file)} <span class="note">· ${escapeHtml(item.threat)} · ${escapeHtml(formatWhen(new Date(item.date * 1000).toISOString()))}</span></li>`,
        )
        .join("")}</ul><p class="note">Ils sont dans le dossier caché .quarantaine-antivirus du disque de données.</p>`
    : t`<p class="note">Aucune menace trouvée.</p>`;
  return t`<p class="note">${
    summary.last_scan
      ? t`Dernière analyse : ${escapeHtml(formatWhen(new Date(summary.last_scan * 1000).toISOString()))} · ${summary.scans} fichier${summary.scans > 1 ? "s" : ""} analysé${summary.scans > 1 ? "s" : ""} en tout.`
      : t(
          "Pas encore d'analyse : les nouveaux fichiers sont analysés chaque heure (le premier démarrage prend quelques minutes).",
        )
  } Les fichiers de plus de 100 Mo (vidéos) ne sont pas analysés.</p>${threats}
    ${disksHtml(summary.disks)}
    ${state.admin && state.admin.rank === "super" ? t`<button class="lien" type="button" id="retirer-antivirus">Retirer l'antivirus</button>` : ""}`;
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
            title: t("Retirer l'antivirus ?"),
            text: t("Les dépôts des invités ne seront plus analysés. Les fichiers mis à l'écart restent où ils sont."),
            confirmLabel: t("Retirer"),
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
