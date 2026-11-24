// Écran Disques : disques branchés, lecture seule ou écriture, éjection.
//
// Sommaire :
//   partagé : pages.disques (écran)
//   interne : partitionsHtml, disksRefresh (la carte Santé des disques : components/disk_health_card.js)
import { confirmAction, dialogElement, toast } from "../components/dialogs.js";
import { diskHealthCard } from "../components/disk_health_card.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatBytes, formatPercent } from "../core/format.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { loadingHtml } from "../core/tasks.js";
import { t } from "../core/i18n.js";

/** Les lignes des partitions d'un disque (nom, usage, réglages des disques de Jabba). */
function partitionsHtml(partitions) {
  return partitions
    .map(
      (item) => `
  <div class="part">
    <div><strong>${escapeHtml(item.label || item.name)}</strong> <span class="note">· ${escapeHtml(item.type || "?")} · ${formatBytes(item.size)}</span><div class="note">${escapeHtml(item.role)}${item.mount && item.role !== item.mount ? " · " + escapeHtml(item.mount) : ""}</div></div>
    <div class="note">${item.usage ? formatBytes(item.usage.used) + t(" utilisés") : ""}</div>
    ${item.usage ? `<div class="jauge"><span style="width:${formatPercent(item.usage)}%"></span></div>` : ""}
    ${
      item.jabba_disk
        ? t`<div class="reglages-disque">
      <label class="ligne-reglage"><span><strong>Écriture autorisée</strong><br><span class="note">${item.jabba_disk.writable ? t("On peut ajouter et modifier des fichiers. Éjecte avant de débrancher.") : t("Lecture seule : tu peux débrancher à tout moment.")}</span></span>
        <button class="interrupteur" role="switch" aria-checked="${item.jabba_disk.writable}" data-mode="${escapeHtml(item.jabba_disk.name)}"><span></span></button></label>
      <div class="actions"><button class="btn" data-ejecter="${escapeHtml(item.jabba_disk.name)}">Éjecter</button>
      ${item.jabba_disk.auto ? t`<button class="btn btn-danger" data-oublier="${escapeHtml(item.jabba_disk.name)}">Oublier ce disque</button>` : ""}</div>
    </div>`
        : ""
    }
    ${item.forgotten ? t`<div class="reglages-disque"><span class="note">Disque oublié : il n'est plus ajouté tout seul.</span><button class="btn" data-reprendre="${escapeHtml(item.forgotten)}">Utiliser ce disque</button></div>` : ""}
    ${item.unreadable && !item.mount ? t`<div class="reglages-disque"><span class="note">Format ${escapeHtml(item.unreadable)} : illisible par le serveur. Pour l'utiliser ici, reformate-le en exFAT depuis le Mac (Utilitaire de disque).</span></div>` : ""}
  </div>`,
    )
    .join("");
}

let disksRefresh = 0;
pages.disques = async () => {
  const tick = ++disksRefresh;
  if (!content.querySelector(".disque"))
    content.innerHTML = t`<div class="entete"><div><h1>Disques</h1></div></div>${loadingHtml()}`;
  const disks = await api("/api/disques");
  // Un disque qu'on branche apparaît tout seul : on regarde toutes les 10 secondes.
  const fingerprint = JSON.stringify(disks);
  setTimeout(async function follow() {
    if (tick !== disksRefresh || !location.hash.startsWith("#/disques")) return;
    if (dialogElement.open) return setTimeout(follow, 10000);
    try {
      if (JSON.stringify(await api("/api/disques")) !== fingerprint) return pages.disques().catch(showError);
    } catch (_) {
      /* on réessaie */
    }
    setTimeout(follow, 10000);
  }, 10000);
  content.innerHTML = t`
    <div class="entete"><div><h1>Disques</h1><p class="sous-titre">Ce qui est branché sur le serveur</p></div>
      <button class="btn" id="rafraichir">Actualiser</button></div>
    <section class="carte pad" id="sante-disques"><h2>Santé des disques</h2><div class="squelette" style="margin-top:10px"></div></section>
    <div class="grille g2">${disks
      .map(
        (item) => `
      <article class="carte pad disque">
        <div class="entete"><div><h2>${escapeHtml(item.title || item.model || item.name)}</h2><div class="note">${escapeHtml(item.genre || "")} · ${formatBytes(item.size)}${item.model ? " · " + escapeHtml(item.model) : ""}</div></div></div>
        <div class="parts">${
          item.partitions.length
            ? partitionsHtml(item.partitions.filter((part) => !part.technical)) +
              (item.partitions.some((part) => part.technical)
                ? t`<details class="parts-techniques"><summary class="note">Partitions techniques (${item.partitions.filter((part) => part.technical).length}) : on n'y touche pas</summary>${partitionsHtml(item.partitions.filter((part) => part.technical))}</details>`
                : "")
            : t`<div class="note part">Aucune partition lisible</div>`
        }
        </div>
      </article>`,
      )
      .join("")}
    </div>
    <p class="note">Branche un disque USB : il est ajouté tout seul en quelques secondes, en lecture seule, et apparaît dans Fichiers, Nextcloud et Jellyfin (dossier /disques). Formats lisibles : exFAT, FAT, NTFS, ext4. Le réglage « Écriture autorisée » et « Oublier » demandent le mot de passe du serveur ; « Éjecter » est toujours libre.</p>`;
  $("#rafraichir").onclick = () => pages.disques().catch(showError);
  diskHealthCard().catch((error) => {
    const element = $("#sante-disques");
    if (element) element.innerHTML = t`<h2>Santé des disques</h2><p class="note">${escapeHtml(error.message)}</p>`;
  });
  content.querySelectorAll("[data-mode]").forEach((element) =>
    element.addEventListener("click", async () => {
      const name = element.dataset.mode;
      const writable = element.getAttribute("aria-checked") !== "true";
      if (
        writable &&
        !(await confirmAction({
          title: t`Autoriser l'écriture sur ${name} ?`,
          text: t(
            "Tu pourras ajouter et modifier des fichiers depuis l'appli, Nextcloud et le Finder. Avant de débrancher le disque, il faudra cliquer sur « Éjecter ». Jellyfin, lui, reste en lecture seule.",
          ),
          confirmLabel: t("Autoriser"),
        }))
      )
        return;
      element.disabled = true;
      try {
        await api(`/api/disques/${encodeURIComponent(name)}/mode`, { method: "POST", json: { writable } });
        toast(writable ? t`${name} : écriture autorisée` : t`${name} : lecture seule`);
      } catch (error) {
        toast(error.message, true);
      }
      pages.disques().catch(showError);
    }),
  );
  content.querySelectorAll("[data-oublier]").forEach((element) =>
    element.addEventListener("click", async () => {
      const name = element.dataset.oublier;
      if (
        !(await confirmAction({
          title: t`Oublier ${name} ?`,
          text: t(
            "Il disparaît de l'appli, de Nextcloud et de Jellyfin, et ne sera plus ajouté tout seul quand tu le branches. Son contenu n'est pas touché.",
          ),
          confirmLabel: t("Oublier"),
          danger: true,
        }))
      )
        return;
      try {
        await api(`/api/disques/${encodeURIComponent(name)}/oublier`, { method: "POST" });
        toast(t`${name} oublié`);
      } catch (error) {
        toast(error.message, true);
      }
      pages.disques().catch(showError);
    }),
  );
  content.querySelectorAll("[data-reprendre]").forEach((element) =>
    element.addEventListener("click", async () => {
      try {
        await api("/api/disques/reprendre", { method: "POST", json: { uuid: element.dataset.reprendre } });
        toast(t("Le disque sera ajouté dans quelques secondes"));
      } catch (error) {
        toast(error.message, true);
      }
      setTimeout(() => pages.disques().catch(showError), 4000);
    }),
  );
  content.querySelectorAll("[data-ejecter]").forEach((element) =>
    element.addEventListener("click", async () => {
      const name = element.dataset.ejecter;
      element.disabled = true;
      element.textContent = t("Éjection…");
      try {
        await api(`/api/disques/${encodeURIComponent(name)}/ejecter`, { method: "POST" });
        await confirmAction({
          title: t`Tu peux débrancher ${name}`,
          text: t("Toutes les copies sont terminées. Rebranché, il reviendra tout seul."),
          confirmLabel: "OK",
        });
      } catch (error) {
        toast(error.message, true);
      }
      pages.disques().catch(showError);
    }),
  );
};
