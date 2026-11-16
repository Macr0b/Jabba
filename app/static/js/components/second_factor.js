// Double vérification : la 2e étape de la connexion, et la carte de réglage (Système).
//
// Sommaire :
//   partagé : secondLoginStep, secondFactorCard
//   interne : toBuffer, toBase64url, creationOptions, requestOptions, credentialJson,
//             deviceName, showRecoveryCodes, addPasskey, addAuthenticatorApp, clearForOtherAccount
import { api, formBody } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { state } from "../core/state.js";
import { confirmAction, dialogElement, dialogForm, toast } from "./dialogs.js";

const METHOD_LABELS = {
  appli: "Code de l'appli d'authentification",
  secours: "Code de secours (8 lettres)",
};
const passkeysAvailable = () => Boolean(window.PublicKeyCredential && navigator.credentials);

// ------------------------------------------------------------ clés d'accès : conversions pour le navigateur
function toBuffer(base64url) {
  const base64 = base64url.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0)).buffer;
}

function toBase64url(buffer) {
  if (!buffer) return null;
  let text = "";
  new Uint8Array(buffer).forEach((byte) => (text += String.fromCharCode(byte)));
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function creationOptions(options) {
  return {
    ...options,
    challenge: toBuffer(options.challenge),
    user: { ...options.user, id: toBuffer(options.user.id) },
    excludeCredentials: (options.excludeCredentials || []).map((item) => ({ ...item, id: toBuffer(item.id) })),
  };
}

function requestOptions(options) {
  return {
    ...options,
    challenge: toBuffer(options.challenge),
    allowCredentials: (options.allowCredentials || []).map((item) => ({ ...item, id: toBuffer(item.id) })),
  };
}

function credentialJson(credential) {
  const response = {};
  for (const name of ["clientDataJSON", "attestationObject", "authenticatorData", "signature", "userHandle"])
    if (credential.response[name]) response[name] = toBase64url(credential.response[name]);
  return { id: credential.id, rawId: toBase64url(credential.rawId), type: credential.type, response };
}

function deviceName() {
  const agent = navigator.userAgent;
  if (/iPhone/.test(agent)) return "iPhone";
  if (/iPad/.test(agent) || (/Macintosh/.test(agent) && navigator.maxTouchPoints > 1)) return "iPad";
  if (/Macintosh/.test(agent)) return "Mac";
  if (/Android/.test(agent)) return "Android";
  if (/Windows/.test(agent)) return "PC Windows";
  return "Clé d'accès";
}

// ------------------------------------------------------------ connexion : 2e étape
// data = {etape, methodes} renvoyé par /api/admin/connexion. Renvoie la session ouverte, ou null.
export function secondLoginStep(data) {
  const methods = data.methodes;
  let codeMethod = methods.includes("appli") ? "appli" : "secours";
  return new Promise((resolve) => {
    let result = null;
    const withKey = methods.includes("cle") && passkeysAvailable();
    dialogForm.innerHTML = `<h2>Deuxième vérification</h2>
      <p>Ton mot de passe est bon. Prouve maintenant que c'est bien toi${withKey ? " avec Face ID, Touch ID ou ta clé" : " avec un code"}.</p>
      ${withKey ? '<button class="btn btn-p bouton-large" type="button" id="avec-cle">Utiliser Face ID ou Touch ID</button>' : ""}
      <label class="champ"><span id="code-label">${METHOD_LABELS[codeMethod]}</span>
        <input type="text" name="code" id="code-double" autocomplete="one-time-code" inputmode="${codeMethod === "appli" ? "numeric" : "text"}" autocapitalize="off" spellcheck="false"></label>
      ${methods.includes("appli") && methods.includes("secours") ? '<button class="lien" type="button" id="autre-code">Utiliser un code de secours</button>' : ""}
      <div class="actions"><button class="btn" value="non" type="submit" formnovalidate>Annuler</button>
      <button class="btn${withKey ? "" : " btn-p"}" value="oui" type="submit">Valider le code</button></div>`;
    const send = async (json) => {
      try {
        result = await api("/api/admin/verification", { method: "POST", json: { etape: data.etape, ...json } }, true);
        dialogElement.close("fini");
      } catch (error) {
        toast(error.message, true);
        if (/expirée/.test(error.message)) dialogElement.close("non");
      }
    };
    const otherCode = $("#autre-code");
    if (otherCode)
      otherCode.onclick = () => {
        codeMethod = codeMethod === "appli" ? "secours" : "appli";
        $("#code-label").textContent = METHOD_LABELS[codeMethod];
        $("#code-double").inputMode = codeMethod === "appli" ? "numeric" : "text";
        otherCode.textContent =
          codeMethod === "appli" ? "Utiliser un code de secours" : "Utiliser l'appli d'authentification";
        $("#code-double").focus();
      };
    const keyButton = $("#avec-cle");
    if (keyButton)
      keyButton.onclick = async () => {
        try {
          const options = await api("/api/admin/cle/options", { method: "POST", json: { etape: data.etape } }, true);
          const credential = await navigator.credentials.get({ publicKey: requestOptions(options) });
          await send({ methode: "cle", credential: credentialJson(credential) });
        } catch (error) {
          toast(error.name === "NotAllowedError" ? "Vérification annulée." : error.message, true);
        }
      };
    dialogForm.onsubmit = (event) => {
      if (event.submitter && event.submitter.value === "oui") {
        event.preventDefault();
        send({ methode: codeMethod, code: $("#code-double").value });
      }
    };
    dialogElement.onclose = () => {
      dialogForm.onsubmit = null;
      resolve(result);
    };
    dialogElement.returnValue = "";
    dialogElement.showModal();
    if (!withKey) $("#code-double").focus();
  });
}

// ------------------------------------------------------------ réglage
async function showRecoveryCodes(codes) {
  if (!codes) return;
  await new Promise((resolve) => {
    dialogForm.innerHTML = `<h2>Tes codes de secours</h2>
      <p>Chaque code sert <strong>une seule fois</strong>, si tu n'as plus ton téléphone. Ils ne seront <strong>plus jamais affichés</strong> : enregistre-les dans l'app Mots de passe (notes de l'entrée Jabba) ou imprime-les.</p>
      <ol class="codes-secours">${codes.map((code) => `<li>${escapeHtml(code)}</li>`).join("")}</ol>
      <div class="actions"><button class="btn" type="button" id="copier-codes">Copier</button><button class="btn btn-p" value="ok" type="submit">C'est noté</button></div>`;
    dialogElement.onclose = () => resolve();
    dialogElement.returnValue = "";
    dialogElement.showModal();
    $("#copier-codes").onclick = async () => {
      try {
        await navigator.clipboard.writeText(codes.join("\n"));
        toast("Codes copiés");
      } catch (_) {
        toast("Copie impossible : sélectionne-les à la main.", true);
      }
    };
  });
}

async function addPasskey() {
  const name = await formBody({
    titre: "Ajouter Face ID ou Touch ID",
    texte:
      "Ton appareil va te demander ton visage, ton doigt ou son code. Seule une clé publique est envoyée au serveur : ton visage et ton empreinte ne quittent jamais l'appareil.",
    champs: [{ nom: "nom", label: "Nom de cet appareil", valeur: deviceName() }],
    valider: "Continuer",
  });
  if (!name || name === "secondaire") return;
  try {
    const options = await api("/api/double/cle/debut", { method: "POST" });
    const credential = await navigator.credentials.create({ publicKey: creationOptions(options) });
    const result = await api("/api/double/cle/fin", {
      method: "POST",
      json: { credential: credentialJson(credential), nom: name.nom },
    });
    toast("Clé d'accès ajoutée");
    await showRecoveryCodes(result.codes);
  } catch (error) {
    toast(error.name === "NotAllowedError" ? "Ajout annulé." : error.message, true);
  }
}

async function addAuthenticatorApp() {
  let started;
  try {
    started = await api("/api/double/appli/debut", { method: "POST" });
  } catch (error) {
    return toast(error.message, true);
  }
  const spaced = started.secret.match(/.{1,4}/g).join(" ");
  const codes = await new Promise((resolve) => {
    let result = null;
    dialogForm.innerHTML = `<h2>Ajouter une appli d'authentification</h2>
      <p>Dans ton appli (Mots de passe d'Apple, Google Authenticator, Microsoft Authenticator…), ajoute un compte en scannant ce QR code. Sur ce même téléphone : touche « Copier la clé » et colle-la dans l'appli.</p>
      <img class="qr-double" src="/api/double/appli/qr.svg?t=${Date.now()}" alt="QR code à scanner avec l'appli d'authentification" width="220" height="220">
      <p class="note">Clé : <code class="cle-texte">${escapeHtml(spaced)}</code> <button class="lien" type="button" id="copier-cle">Copier la clé</button></p>
      <label class="champ"><span>Code à 6 chiffres affiché par l'appli</span>
        <input type="text" name="code" id="code-appli" autocomplete="one-time-code" inputmode="numeric" autocapitalize="off"></label>
      <div class="actions"><button class="btn" value="non" type="submit" formnovalidate>Annuler</button>
      <button class="btn btn-p" value="oui" type="submit">Activer</button></div>`;
    $("#copier-cle").onclick = async () => {
      try {
        await navigator.clipboard.writeText(started.secret);
        toast("Clé copiée");
      } catch (_) {
        toast("Copie impossible : recopie-la à la main.", true);
      }
    };
    dialogForm.onsubmit = async (event) => {
      if (!event.submitter || event.submitter.value !== "oui") return;
      event.preventDefault();
      try {
        const answer = await api("/api/double/appli/fin", {
          method: "POST",
          json: { code: $("#code-appli").value },
        });
        result = { codes: answer.codes };
        dialogElement.close("fini");
      } catch (error) {
        toast(error.message, true);
      }
    };
    dialogElement.onclose = () => {
      dialogForm.onsubmit = null;
      resolve(result);
    };
    dialogElement.returnValue = "";
    dialogElement.showModal();
    $("#code-appli").focus();
  });
  if (!codes) return;
  toast("Appli d'authentification activée");
  await showRecoveryCodes(codes.codes);
}

async function clearForOtherAccount() {
  const answers = await formBody({
    titre: "Retirer la double vérification d'un compte",
    texte:
      "Pour quelqu'un qui a perdu son téléphone et ses codes de secours. Il se connectera avec son seul mot de passe, puis pourra en ajouter une nouvelle.",
    champs: [{ nom: "identifiant", label: "Identifiant du compte" }],
    valider: "Retirer",
  });
  if (!answers || answers === "secondaire") return;
  try {
    await api("/api/double/effacer", { method: "POST", json: { identifiant: answers.identifiant } });
    toast("Double vérification retirée");
  } catch (error) {
    toast(error.message, true);
  }
}

// La carte « Double vérification » de l'écran Système.
export async function secondFactorCard() {
  const element = $("#double");
  if (!element) return;
  const summary = await api("/api/double");
  const lines = [
    ...summary.cles.map(
      (
        key,
      ) => `<div class="appareil"><span>${escapeHtml(key.nom)} <span class="note">· Face ID, Touch ID ou clé</span></span>
        <button class="btn btn-petit" type="button" data-retirer-cle="${escapeHtml(key.id)}">Retirer</button></div>`,
    ),
    summary.appli
      ? `<div class="appareil"><span>Appli d'authentification</span><button class="btn btn-petit" type="button" id="retirer-appli">Retirer</button></div>`
      : "",
    summary.active
      ? `<div class="appareil"><span>Codes de secours <span class="note">· ${summary.codes_restants} restant${summary.codes_restants > 1 ? "s" : ""}</span></span>
        <button class="btn btn-petit" type="button" id="nouveaux-codes">Nouveaux codes</button></div>`
      : "",
  ].join("");
  element.innerHTML = `<h2>Double vérification</h2>
    <p class="note" style="margin:6px 0 12px">${
      summary.active
        ? "Active : après ton mot de passe, Jabba demande une deuxième preuve."
        : "Après ton mot de passe, Jabba demandera une deuxième preuve : Face ID, Touch ID, ou le code d'une appli d'authentification. Un mot de passe volé ne suffira plus."
    }</p>
    ${summary.active && summary.codes_restants < 3 ? '<div class="bandeau" style="margin-bottom:12px"><span>Il te reste peu de codes de secours : crées-en de nouveaux.</span></div>' : ""}
    <div class="liste-appareils">${lines}</div>
    <div class="actions" style="margin-top:12px">
      ${passkeysAvailable() ? '<button class="btn" type="button" id="ajouter-cle">Ajouter Face ID ou Touch ID</button>' : ""}
      ${summary.appli ? "" : '<button class="btn" type="button" id="ajouter-appli">Ajouter une appli d\'authentification</button>'}
      ${state.admin && state.admin.rang === "super" ? '<button class="lien" type="button" id="effacer-autre">Téléphone perdu (autre compte)</button>' : ""}
    </div>`;
  const refresh = () => secondFactorCard().catch((error) => toast(error.message, true));
  const bind = (selector, action) => {
    const button = element.querySelector(selector);
    if (button) button.onclick = () => action(button).then(refresh);
  };
  bind("#ajouter-cle", addPasskey);
  bind("#ajouter-appli", addAuthenticatorApp);
  bind("#effacer-autre", clearForOtherAccount);
  bind("#nouveaux-codes", async () => {
    const ok = await confirmAction({
      titre: "Créer de nouveaux codes de secours ?",
      texte: "Les anciens codes ne marcheront plus.",
      valider: "Créer",
    });
    if (!ok) return;
    try {
      const result = await api("/api/double/codes", { method: "POST" });
      await showRecoveryCodes(result.codes);
    } catch (error) {
      toast(error.message, true);
    }
  });
  bind("#retirer-appli", async () => {
    if (!(await confirmAction({ titre: "Retirer l'appli d'authentification ?", valider: "Retirer", danger: true })))
      return;
    try {
      await api("/api/double/appli/retirer", { method: "POST" });
    } catch (error) {
      toast(error.message, true);
    }
  });
  element.querySelectorAll("[data-retirer-cle]").forEach((button) => {
    button.onclick = async () => {
      if (!(await confirmAction({ titre: "Retirer cette clé d'accès ?", valider: "Retirer", danger: true }))) return;
      try {
        await api(`/api/double/cle/${encodeURIComponent(button.dataset.retirerCle)}/retirer`, { method: "POST" });
      } catch (error) {
        toast(error.message, true);
      }
      refresh();
    };
  });
}
