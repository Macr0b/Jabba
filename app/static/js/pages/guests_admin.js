// Écran Invités (admin) : code d'accès, dossiers partagés, outils ouverts au Wi-Fi.
//
// Sommaire :
//   partagé : pages.invites (écran)
//   interne : chooseFolder
import { confirmAction, dialogForm, dialogElement, chooseFromMenu, toast } from "../components/dialogs.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { loadingHtml } from "../core/tasks.js";
import { t } from "../core/i18n.js";

// ------------------------------------------------------------ invités : réglages (admin)
pages.invites = async () => {
  content.innerHTML = t`<div class="entete"><div><h1>Invités</h1></div></div>${loadingHtml(4)}`;
  const settings = await api("/api/invites");
  content.innerHTML = t`
    <div class="entete"><div><h1>Invités</h1><p class="sous-titre">Ce que voient les personnes connectées à ton Wi-Fi, sans compte ni application.</p></div></div>
    <section class="carte pad">
      <label class="ligne-reglage"><span><strong>Accès invités</strong><br><span class="note">${settings.active ? t("Ouvert : avec le code, un invité sur ton Wi-Fi voit les dossiers ci-dessous.") : t("Fermé : la page invités n'affiche rien.")}</span></span>
        <button class="interrupteur" role="switch" aria-checked="${settings.active}" id="actif"><span></span></button></label>
    </section>
    ${
      settings.active
        ? t`<section class="carte pad acces-invites">
      <div class="qr"><img src="/api/invites/qr.svg?c=${escapeHtml(settings.code)}" alt="QR code de l'accès invités" width="180" height="180"></div>
      <div><h2>Code d'accès : <span class="code-invite">${escapeHtml(settings.code)}</span></h2>
        <p class="note">Les invités scannent le QR code, connectés à ton Wi-Fi principal (pas le Wi-Fi invités de la box, isolé du serveur). Sans téléphone : ouvrir <strong>${escapeHtml(settings.url ? settings.url.split("/?")[0] : settings.local_name)}</strong> ou <strong>${escapeHtml(settings.local_name)}</strong>, puis taper le code.</p>
        <p class="note">Un appareil reste reconnu 7 jours. Changer le code fait sortir tout le monde.</p>
        <div class="actions"><button class="btn" id="code">Nouveau code</button><button class="btn" id="imprimer">Imprimer</button></div></div>
    </section>`
        : ""
    }
    <h2 class="titre-section">Dossiers partagés</h2>
    <section class="carte tableau">
      ${
        settings.folders
          .map(
            (
              item,
            ) => t`<div class="ligne"><span class="nom"><span class="ic-dossier" aria-hidden="true"></span><span>${escapeHtml(item.name)}<span class="detail-chemin">${escapeHtml(item.path)}</span></span></span>
        <span class="col-l"><select data-mode="${escapeHtml(item.id)}" aria-label="Droits sur ${escapeHtml(item.name)}"><option value="lecture"${item.mode === "lecture" ? " selected" : ""}>Lecture seule</option><option value="depot"${item.mode === "depot" ? " selected" : ""}>Lire et déposer</option></select></span>
        <span></span><button class="btn-menu" data-retirer="${escapeHtml(item.id)}" aria-label="Retirer ${escapeHtml(item.name)}">✕</button></div>`,
          )
          .join("") || t('<div class="vide">Aucun dossier partagé.</div>')
      }
      <div class="depot"><button class="btn" id="ajouter-dossier">Partager un dossier</button></div>
    </section>
    <h2 class="titre-section">Outils sur le Wi-Fi</h2>
    <section class="carte pad">
      <p class="note" style="margin:0 0 12px">Un outil coché est joignable depuis ton Wi-Fi (par les invités, la télé…) et apparaît sur leur page. Décoché, il n'est joignable que par Tailscale. Chaque outil garde son propre mot de passe.</p>
      ${
        settings.tools
          .map(
            (
              item,
            ) => `<label class="ligne-reglage" style="margin-bottom:12px"><span><strong>${escapeHtml(item.name)}</strong> <span class="note">· port ${item.port}${item.active ? "" : t(" · désactivé dans l'établi")}</span><br><span class="note">${escapeHtml(item.role)}</span></span>
        <button class="interrupteur" role="switch" aria-checked="${item.wifi}" data-wifi="${escapeHtml(item.id)}"><span></span></button></label>`,
          )
          .join("") || t('<p class="note">Aucun outil.</p>')
      }
    </section>`;
  const reload = () => pages.invites().catch(showError);
  const send = async (body, ok) => {
    try {
      await api("/api/invites", { method: "POST", json: body });
      if (ok) toast(ok);
    } catch (error) {
      toast(error.message, true);
    }
    reload();
  };
  $("#actif").onclick = () =>
    send(
      { action: "actif", active: !settings.active },
      settings.active ? t("Accès invités fermé") : t("Accès invités ouvert"),
    );
  const code = $("#code");
  if (code)
    code.onclick = async () => {
      if (
        await confirmAction({
          title: t("Nouveau code ?"),
          text: t("Les invités déjà entrés devront saisir le nouveau code, et l'ancien QR code ne marchera plus."),
          confirmLabel: t("Changer le code"),
        })
      )
        send({ action: "code" }, "Nouveau code créé");
    };
  const printButton = $("#imprimer");
  if (printButton)
    printButton.onclick = () => {
      const printWindow = window.open("", "_blank");
      if (!printWindow) return toast(t("Autorise les fenêtres pour imprimer."), true);
      printWindow.document.write(
        t`<title>Accès invités</title><body style="font-family:-apple-system,sans-serif;text-align:center;padding:40px"><h1>Wi-Fi : fichiers partagés</h1><img src="${location.origin}/api/invites/qr.svg" width="320"><p style="font-size:22px">Code : <b>${escapeHtml(settings.code)}</b></p><p>${escapeHtml(settings.url ? settings.url.split("/?")[0] : settings.local_name)}</p></body>`,
      );
      printWindow.document.close();
      // Impression dès que le QR code est affiché (lancée d'ici : aucun script dans la page imprimée).
      const qrImage = printWindow.document.querySelector("img");
      if (qrImage.complete) printWindow.print();
      else qrImage.onload = () => printWindow.print();
    };
  content
    .querySelectorAll("[data-mode]")
    .forEach((selected) =>
      selected.addEventListener("change", () =>
        send({ action: "mode_dossier", id: selected.dataset.mode, mode: selected.value }, "Enregistré"),
      ),
    );
  content.querySelectorAll("[data-retirer]").forEach((element) =>
    element.addEventListener("click", async () => {
      if (
        await confirmAction({
          title: t("Ne plus partager ce dossier ?"),
          text: t("Les invités ne le verront plus. Son contenu n'est pas touché."),
          confirmLabel: t("Retirer"),
        })
      )
        send({ action: "retirer_dossier", id: element.dataset.retirer }, "Dossier retiré");
    }),
  );
  content
    .querySelectorAll("[data-wifi]")
    .forEach((element) =>
      element.addEventListener("click", () =>
        send(
          { action: "wifi", outil: element.dataset.wifi, wifi: element.getAttribute("aria-checked") !== "true" },
          "Enregistré",
        ),
      ),
    );
  $("#ajouter-dossier").onclick = async () => {
    const path = await chooseFolder();
    if (!path) return;
    const item = await chooseFromMenu(t`Partager « ${path.split("/").pop()} »`, [
      { text: t("En lecture seule"), mode: "lecture" },
      { text: t("Lire et déposer"), mode: "depot" },
    ]);
    if (item) send({ action: "ajouter_dossier", path, mode: item.mode }, "Dossier partagé");
  };
};

// Petite fenêtre pour choisir un dossier du serveur.
function chooseFolder(path = "/") {
  return new Promise(async (resolve) => {
    let data;
    try {
      data = await api("/api/invites/parcourir?chemin=" + encodeURIComponent(path));
    } catch (error) {
      toast(error.message, true);
      return resolve(null);
    }
    const parent = path === "/" ? null : path.split("/").slice(0, -1).join("/") || "/";
    dialogForm.innerHTML = t`<h2>Choisir un dossier</h2><p class="note">${escapeHtml(path === "/" ? t("Dossiers du serveur") : path)}</p>
      <div class="liste-dossiers">${parent !== null ? t`<button class="btn" type="button" data-aller="${escapeHtml(parent)}">‹ Retour</button>` : ""}
      ${data.items.map((item) => `<button class="btn" type="button" data-aller="${escapeHtml(item.path)}"><span class="ic-dossier" aria-hidden="true"></span> ${escapeHtml(item.name)}</button>`).join("") || t('<p class="note">Pas de sous-dossier.</p>')}</div>
      <div class="actions"><button class="btn" value="non" type="submit">Annuler</button>${path !== "/" ? t`<button class="btn btn-p" value="oui" type="submit">Choisir ce dossier</button>` : ""}</div>`;
    dialogElement.onclose = () => resolve(dialogElement.returnValue === "oui" ? path : null);
    dialogElement.returnValue = "";
    if (!dialogElement.open) dialogElement.showModal();
    dialogForm.querySelectorAll("[data-aller]").forEach((element) =>
      element.addEventListener("click", () => {
        dialogElement.onclose = null;
        dialogElement.close();
        chooseFolder(element.dataset.aller).then(resolve);
      }),
    );
  });
}
