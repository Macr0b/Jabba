// Accès par le domaine, dans l'Établi : Nextcloud joignable depuis Internet, en HTTPS.
// Jabba, lui, reste réservé à Tailscale. Chacun saisit son propre domaine (rien dans le code).
//
// Sommaire :
//   partagé : domainCardHtml, bindDomain
//   interne : formHtml, checklistHtml, stepHtml, ovhHtml
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { state } from "../core/state.js";
import { confirmAction, toast } from "./dialogs.js";
import { toolIcon } from "../pages/home.js";

export function domainCardHtml() {
  return t`<h2 class="titre-section">Accès depuis Internet</h2>
    <div class="grille g2"><article class="carte pad outil" id="carte-domaine">
      <div class="haut"><div class="titre-outil">${toolIcon({ nom: t("Domaine") })}<div><h2>Accès par le domaine</h2><div class="role">Nextcloud joignable de partout, en HTTPS (Jabba reste privé)</div></div></div></div>
      <div id="domaine-detail"><p class="note">Chargement…</p></div>
    </article></div>`;
}

/** Une étape de la liste : faite ou à faire, avec ce qu'il faut faire. */
function stepHtml(done, title, text, waiting = t("À faire")) {
  return `<li class="etape-domaine${done ? " faite" : ""}">
    <span class="pastille ${done ? "p-ok" : "p-gris"}">${done ? t("Fait") : waiting}</span>
    <div><strong>${title}</strong><p class="note">${text}</p></div></li>`;
}

function formHtml(settings, isSuper) {
  if (!isSuper) return t`<p class="note">Seul le super admin peut ouvrir Nextcloud sur Internet.</p>`;
  return t`<p class="note">Indique ton nom de domaine (acheté chez OVH, Gandi…). Seul Nextcloud sera ouvert, à l'adresse « préfixe.domaine ». Jabba, lui, reste réservé à Tailscale.</p>
    <div class="formulaire-domaine">
      <label class="champ"><span>Domaine</span><input type="text" id="domaine-nom" value="${escapeHtml(settings.domaine || "")}" placeholder="exemple.fr" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
      <label class="champ"><span>Préfixe pour Nextcloud</span><input type="text" id="domaine-prefixe" value="${escapeHtml(settings.prefixe || "cloud")}" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
    </div>
    <div class="actions"><button class="btn btn-p" type="button" id="domaine-activer">Ouvrir Nextcloud sur Internet</button></div>`;
}

function checklistHtml(settings, isSuper) {
  const address = escapeHtml(settings.adresse);
  const box = escapeHtml(settings.ip_box || t("inconnue"));
  const prefix = escapeHtml(settings.prefixe || "@");
  const pointing = settings.dns.length ? escapeHtml(settings.dns.join(", ")) : t("rien pour l'instant");
  const ready = settings.dns_ok && settings.certificat_ok;
  return t`<p><span class="pastille ${ready ? "p-ok" : "p-alerte"}">${ready ? t("En ligne") : t("En préparation")}</span>
      ${ready ? `<a href="https://${address}" target="_blank" rel="noopener">https://${address}</a>` : address}</p>
    <ol class="etapes-domaine">
      ${stepHtml(
        Boolean(settings.ip_box),
        t("Une adresse Internet à toi"),
        t`Adresse de ta box : <strong>${box}</strong>. Si ton fournisseur la partage entre plusieurs abonnés (chez Free : « IPv4 partagée »), demande une adresse « full-stack » (Free : Espace abonné → Ma Freebox), depuis un appareil connecté à la box, puis redémarre la box.`,
      )}
      ${stepHtml(
        settings.dns_ok,
        t("Le domaine pointe vers ta box"),
        t`Chez ton fournisseur de domaine (OVH : Zone DNS → Ajouter une entrée → A) : sous-domaine <strong>${prefix}</strong>, cible <strong>${box}</strong>. Aujourd'hui, ${address} pointe vers : ${pointing}. La prise en compte prend de quelques minutes à quelques heures.`,
      )}
      ${stepHtml(
        settings.certificat_ok,
        t("La box laisse passer HTTPS vers le serveur"),
        t`Dans l'interface de la box (Freebox OS : Paramètres → Gestion des ports) : port externe <strong>443</strong>, TCP, vers <strong>${escapeHtml(settings.ip_serveur || t("le serveur"))}</strong>, port <strong>${settings.port_serveur}</strong>. Rien d'autre à ouvrir. Jabba ne voit pas dans la box : cette étape se valide avec le certificat.`,
        t("Se vérifie avec le certificat"),
      )}
      ${stepHtml(
        settings.certificat_ok,
        t("Le certificat HTTPS"),
        t(
          "Obtenu et renouvelé tout seul (Let's Encrypt) dès que les deux étapes d'avant sont faites. Revérifie dans quelques minutes.",
        ),
      )}
    </ol>
    <p class="note">${t("Double vérification : Jabba l'a installée dans Nextcloud. Chacun l'active pour son compte (Paramètres personnels → Sécurité → TOTP), avec une appli de codes comme Authenticator, et garde ses codes de secours.")}</p>
    ${isSuper ? ovhHtml(settings) : ""}
    <div class="actions">
      <button class="btn" type="button" id="domaine-verifier">Revérifier</button>
      ${isSuper ? t`<button class="btn" type="button" id="domaine-retirer">Fermer l'accès</button>` : ""}
    </div>`;
}

/** L'entrée DNS réglée par Jabba chez OVH : une clé créée une fois, puis plus rien à faire. */
function ovhHtml(settings) {
  if (settings.ovh_pret)
    return t`<div class="bloc-ovh"><h3>DNS réglé par Jabba (OVH)</h3>
      <p class="note">Jabba crée l'entrée ${escapeHtml(settings.prefixe || "cloud")} chez OVH et la corrige toute seule si l'adresse de ta box change (vérifié chaque heure).</p>
      <div class="actions"><button class="btn" type="button" id="ovh-regler">Régler maintenant</button><button class="lien" type="button" id="ovh-oublier">Oublier la clé</button></div></div>`;
  return t`<div class="bloc-ovh"><h3>Laisser Jabba régler le DNS (domaine chez OVH)</h3>
    <ol class="note">
      <li>Ouvre la page d'OVH ci-dessous et connecte-toi : les droits sont déjà remplis, limités à la zone DNS de ${escapeHtml(settings.domaine)}. Dans « Validity », choisis <strong>Unlimited</strong>, puis <strong>Create keys</strong>.</li>
      <li>Recopie ici les trois codes affichés par OVH.</li>
    </ol>
    <p><a class="btn" href="${escapeHtml(settings.ovh_lien)}" target="_blank" rel="noopener">Créer la clé chez OVH</a></p>
    <label class="champ"><span>Application key</span><input type="text" id="ovh-ak" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
    <label class="champ"><span>Application secret</span><input type="password" id="ovh-as" autocomplete="off"></label>
    <label class="champ"><span>Consumer key</span><input type="password" id="ovh-ck" autocomplete="off"></label>
    <div class="actions"><button class="btn btn-p" type="button" id="ovh-enregistrer">Enregistrer et régler le DNS</button></div></div>`;
}

export function bindDomain() {
  const detail = $("#domaine-detail");
  if (!detail) return;
  const isSuper = Boolean(state.admin && state.admin.rang === "super");
  const render = (settings) => {
    detail.innerHTML = settings.actif ? checklistHtml(settings, isSuper) : formHtml(settings, isSuper);
    const enable = $("#domaine-activer");
    if (enable)
      enable.onclick = async () => {
        enable.disabled = true;
        try {
          const result = await api("/api/domaine", {
            method: "POST",
            json: { domaine: $("#domaine-nom").value.trim(), prefixe: $("#domaine-prefixe").value.trim() },
          });
          toast(t("Caddy est en place : suis les étapes pour finir."));
          render(result);
        } catch (error) {
          toast(error.message, true);
          enable.disabled = false;
        }
      };
    const check = $("#domaine-verifier");
    if (check) check.onclick = () => load();
    const ovhCall = async (button, path, json, done) => {
      button.disabled = true;
      try {
        render(await api(path, { method: "POST", json }));
        toast(done);
      } catch (error) {
        toast(error.message, true);
        button.disabled = false;
      }
    };
    const saveKeys = $("#ovh-enregistrer");
    if (saveKeys)
      saveKeys.onclick = () =>
        ovhCall(
          saveKeys,
          "/api/domaine/ovh",
          {
            application_key: $("#ovh-ak").value,
            application_secret: $("#ovh-as").value,
            consumer_key: $("#ovh-ck").value,
          },
          t("Clé enregistrée : l'entrée DNS est réglée chez OVH."),
        );
    const syncNow = $("#ovh-regler");
    if (syncNow)
      syncNow.onclick = () => ovhCall(syncNow, "/api/domaine/ovh/regler", {}, t("Entrée DNS vérifiée chez OVH."));
    const forgetKey = $("#ovh-oublier");
    if (forgetKey) forgetKey.onclick = () => ovhCall(forgetKey, "/api/domaine/ovh/oublier", {}, t("Clé OVH oubliée."));
    const remove = $("#domaine-retirer");
    if (remove)
      remove.onclick = async () => {
        const ok = await confirmAction({
          titre: t("Fermer l'accès par le domaine ?"),
          texte: t(
            "Nextcloud ne sera plus joignable depuis Internet (il le reste par Tailscale). Pense aussi à retirer la redirection du port 443 dans la box.",
          ),
          valider: t("Fermer l'accès"),
        });
        if (!ok) return;
        try {
          render(await api("/api/domaine/retirer", { method: "POST" }));
          toast(t("Accès par le domaine fermé"));
        } catch (error) {
          toast(error.message, true);
        }
      };
  };
  const load = async () => {
    try {
      render(await api("/api/domaine"));
    } catch (error) {
      detail.innerHTML = `<p class="note">${escapeHtml(error.message)}</p>`;
    }
  };
  load();
}
