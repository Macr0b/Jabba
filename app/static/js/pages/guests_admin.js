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

// ------------------------------------------------------------ invités : réglages (admin)
pages.invites = async () => {
  content.innerHTML = `<div class="entete"><div><h1>Invités</h1></div></div>${loadingHtml(4)}`;
  const event = await api("/api/invites");
  content.innerHTML = `
    <div class="entete"><div><h1>Invités</h1><p class="sous-titre">Ce que voient les personnes connectées à ton Wi-Fi, sans compte ni application.</p></div></div>
    <section class="carte pad">
      <label class="ligne-reglage"><span><strong>Accès invités</strong><br><span class="note">${event.actif ? "Ouvert : avec le code, un invité sur ton Wi-Fi voit les dossiers ci-dessous." : "Fermé : la page invités n'affiche rien."}</span></span>
        <button class="interrupteur" role="switch" aria-checked="${event.actif}" id="actif"><span></span></button></label>
    </section>
    ${
      event.actif
        ? `<section class="carte pad acces-invites">
      <div class="qr"><img src="/api/invites/qr.svg?c=${escapeHtml(event.code)}" alt="QR code de l'accès invités" width="180" height="180"></div>
      <div><h2>Code d'accès : <span class="code-invite">${escapeHtml(event.code)}</span></h2>
        <p class="note">Les invités scannent le QR code, connectés à ton Wi-Fi principal (pas le Wi-Fi invités de la box, isolé du serveur). Sans téléphone : ouvrir <strong>${escapeHtml(event.url ? event.url.split("/?")[0] : event.nom_local)}</strong> ou <strong>${escapeHtml(event.nom_local)}</strong>, puis taper le code.</p>
        <p class="note">Un appareil reste reconnu 7 jours. Changer le code fait sortir tout le monde.</p>
        <div class="actions"><button class="btn" id="code">Nouveau code</button><button class="btn" id="imprimer">Imprimer</button></div></div>
    </section>`
        : ""
    }
    <h2 class="titre-section">Dossiers partagés</h2>
    <section class="carte tableau">
      ${
        event.dossiers
          .map(
            (
              item,
            ) => `<div class="ligne"><span class="nom"><span class="ic-dossier" aria-hidden="true"></span><span>${escapeHtml(item.nom)}<span class="detail-chemin">${escapeHtml(item.chemin)}</span></span></span>
        <span class="col-l"><select data-mode="${escapeHtml(item.id)}" aria-label="Droits sur ${escapeHtml(item.nom)}"><option value="lecture"${item.mode === "lecture" ? " selected" : ""}>Lecture seule</option><option value="depot"${item.mode === "depot" ? " selected" : ""}>Lire et déposer</option></select></span>
        <span></span><button class="btn-menu" data-retirer="${escapeHtml(item.id)}" aria-label="Retirer ${escapeHtml(item.nom)}">✕</button></div>`,
          )
          .join("") || '<div class="vide">Aucun dossier partagé.</div>'
      }
      <div class="depot"><button class="btn" id="ajouter-dossier">Partager un dossier</button></div>
    </section>
    <h2 class="titre-section">Outils sur le Wi-Fi</h2>
    <section class="carte pad">
      <p class="note" style="margin:0 0 12px">Un outil coché est joignable depuis ton Wi-Fi (par les invités, la télé…) et apparaît sur leur page. Décoché, il n'est joignable que par Tailscale. Chaque outil garde son propre mot de passe.</p>
      ${
        event.outils
          .map(
            (
              item,
            ) => `<label class="ligne-reglage" style="margin-bottom:12px"><span><strong>${escapeHtml(item.nom)}</strong> <span class="note">· port ${item.port}${item.actif ? "" : " · désactivé dans l'établi"}</span><br><span class="note">${escapeHtml(item.role)}</span></span>
        <button class="interrupteur" role="switch" aria-checked="${item.wifi}" data-wifi="${escapeHtml(item.id)}"><span></span></button></label>`,
          )
          .join("") || '<p class="note">Aucun outil.</p>'
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
    send({ action: "actif", actif: !event.actif }, event.actif ? "Accès invités fermé" : "Accès invités ouvert");
  const code = $("#code");
  if (code)
    code.onclick = async () => {
      if (
        await confirmAction({
          titre: "Nouveau code ?",
          texte: "Les invités déjà entrés devront saisir le nouveau code, et l'ancien QR code ne marchera plus.",
          valider: "Changer le code",
        })
      )
        send({ action: "code" }, "Nouveau code créé");
    };
  const important = $("#imprimer");
  if (important)
    important.onclick = () => {
      const width = window.open("", "_blank");
      if (!width) return toast("Autorise les fenêtres pour imprimer.", true);
      width.document.write(
        `<title>Accès invités</title><body style="font-family:-apple-system,sans-serif;text-align:center;padding:40px"><h1>Wi-Fi : fichiers partagés</h1><img src="${location.origin}/api/invites/qr.svg" width="320"><p style="font-size:22px">Code : <b>${escapeHtml(event.code)}</b></p><p>${escapeHtml(event.url ? event.url.split("/?")[0] : event.nom_local)}</p><script>onload=()=>print()<\/script></body>`,
      );
      width.document.close();
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
          titre: "Ne plus partager ce dossier ?",
          texte: "Les invités ne le verront plus. Son contenu n'est pas touché.",
          valider: "Retirer",
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
    const item = await chooseFromMenu(`Partager « ${path.split("/").pop()} »`, [
      { texte: "En lecture seule", mode: "lecture" },
      { texte: "Lire et déposer", mode: "depot" },
    ]);
    if (item) send({ action: "ajouter_dossier", chemin: path, mode: item.mode }, "Dossier partagé");
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
    dialogForm.innerHTML = `<h2>Choisir un dossier</h2><p class="note">${escapeHtml(path === "/" ? "Dossiers du serveur" : path)}</p>
      <div class="liste-dossiers">${parent !== null ? `<button class="btn" type="button" data-aller="${escapeHtml(parent)}">‹ Retour</button>` : ""}
      ${data.elements.map((item) => `<button class="btn" type="button" data-aller="${escapeHtml(item.chemin)}"><span class="ic-dossier" aria-hidden="true"></span> ${escapeHtml(item.nom)}</button>`).join("") || '<p class="note">Pas de sous-dossier.</p>'}</div>
      <div class="actions"><button class="btn" value="non" type="submit">Annuler</button>${path !== "/" ? `<button class="btn btn-p" value="oui" type="submit">Choisir ce dossier</button>` : ""}</div>`;
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
