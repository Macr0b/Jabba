// Accès par le domaine, dans l'Établi : Nextcloud joignable depuis Internet, en HTTPS.
// Jabba, lui, reste réservé à Tailscale. Chacun saisit son propre domaine (rien dans le code).
//
// Sommaire :
//   partagé : domainCardHtml, bindDomain
//   interne : formHtml, checklistHtml, stepHtml, dnsHtml, cloudflareFormHtml, ovhFormHtml
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { state } from "../core/state.js";
import { confirmAction, toast } from "./dialogs.js";
import { toolIcon } from "../pages/home.js";

export function domainCardHtml() {
  return t`<h2 class="titre-section">Accès depuis Internet</h2>
    <div class="grille g2"><article class="carte pad outil" id="carte-domaine">
      <div class="haut"><div class="titre-outil">${toolIcon({ id: "domaine", name: t("Domaine") })}<div><h2>Accès par le domaine</h2><div class="role">Nextcloud joignable de partout, en HTTPS (Jabba reste privé)</div></div></div></div>
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
      <label class="champ"><span>Domaine</span><input type="text" id="domaine-nom" value="${escapeHtml(settings.domain || "")}" placeholder="exemple.fr" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
      <label class="champ"><span>Préfixe pour Nextcloud</span><input type="text" id="domaine-prefixe" value="${escapeHtml(settings.prefix || "cloud")}" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
    </div>
    <div class="actions"><button class="btn btn-p" type="button" id="domaine-activer">Ouvrir Nextcloud sur Internet</button></div>`;
}

function checklistHtml(settings, isSuper) {
  const address = escapeHtml(settings.address);
  const box = escapeHtml(settings.router_ip || t("inconnue"));
  const prefix = escapeHtml(settings.prefix || "@");
  const pointing = settings.dns.length ? escapeHtml(settings.dns.join(", ")) : t("rien pour l'instant");
  const ready = settings.dns_ok && settings.certificate_ok;
  return t`<p><span class="pastille ${ready ? "p-ok" : "p-alerte"}">${ready ? t("En ligne") : t("En préparation")}</span>
      ${ready ? `<a href="https://${address}" target="_blank" rel="noopener">https://${address}</a>` : address}</p>
    <ol class="etapes-domaine">
      ${stepHtml(
        Boolean(settings.router_ip),
        t("Une adresse Internet à toi"),
        t`Adresse de ta box : <strong>${box}</strong>. Si ton fournisseur la partage entre plusieurs abonnés (chez Free : « IPv4 partagée »), demande une adresse « full-stack » (Free : Espace abonné → Ma Freebox), depuis un appareil connecté à la box, puis redémarre la box.`,
      )}
      ${stepHtml(
        settings.dns_ok,
        t("Le domaine pointe vers ta box"),
        t`Chez ton fournisseur de domaine (Cloudflare : DNS → Enregistrements → Ajouter, type A, nuage gris ; OVH : Zone DNS → Ajouter une entrée → A) : sous-domaine <strong>${prefix}</strong>, cible <strong>${box}</strong>. Aujourd'hui, ${address} pointe vers : ${pointing}. La prise en compte prend de quelques minutes à quelques heures.`,
      )}
      ${stepHtml(
        settings.certificate_ok,
        t("La box laisse passer HTTPS vers le serveur"),
        t`Dans l'interface de la box (Freebox OS : Paramètres → Gestion des ports) : port externe <strong>443</strong>, TCP, vers <strong>${escapeHtml(settings.server_ip || t("le serveur"))}</strong>, port <strong>${settings.server_port}</strong>. Rien d'autre à ouvrir. Jabba ne voit pas dans la box : cette étape se valide avec le certificat.`,
        t("Se vérifie avec le certificat"),
      )}
      ${stepHtml(
        settings.certificate_ok,
        t("Le certificat HTTPS"),
        t(
          "Obtenu et renouvelé tout seul (Let's Encrypt) dès que les deux étapes d'avant sont faites. Revérifie dans quelques minutes.",
        ),
      )}
      ${stepHtml(
        settings.protection_ok === true,
        t("Nextcloud freine les essais de mots de passe"),
        settings.protection_ok === false
          ? t(
              "Coupé dans les réglages de Nextcloud : remets « auth.bruteforce.protection.enabled » à true (ou retire-le) et laisse « forwarded_for_headers » par défaut. Jabba a déjà remis Caddy parmi ses relais de confiance.",
            )
          : settings.protection_ok === null
            ? t("Nextcloud ne répond pas pour l'instant : revérifie dans un moment.")
            : t(
                "Nextcloud voit la vraie adresse de chaque visiteur (Caddy est un relai de confiance) et ralentit celui qui enchaîne les mauvais mots de passe, sans gêner les autres. Vérifié chaque heure.",
              ),
      )}
    </ol>
    <p class="note">${t("Double vérification : Jabba l'a installée dans Nextcloud. Chacun l'active pour son compte (Paramètres personnels → Sécurité → TOTP), avec une appli de codes comme Authenticator, et garde ses codes de secours.")}</p>
    ${isSuper ? dnsHtml(settings) : ""}
    <div class="actions">
      <button class="btn" type="button" id="domaine-verifier">Revérifier</button>
      ${isSuper ? t`<button class="btn" type="button" id="domaine-retirer">Fermer l'accès</button>` : ""}
    </div>`;
}

/** L'entrée DNS réglée par Jabba chez Cloudflare ou OVH : un jeton (ou une clé) donné une fois, puis plus rien à faire. */
function dnsHtml(settings) {
  if (!settings.dns_auto) return t`<div class="bloc-ovh">${cloudflareFormHtml(settings)}${ovhFormHtml(settings)}</div>`;
  const prefix = escapeHtml(settings.prefix || "cloud");
  const name = settings.dns_auto === "cloudflare" ? t("Cloudflare") : "OVH";
  // Domaine passé d'OVH à Cloudflare : le jeton se colle ici, la clé OVH est alors oubliée (et révoquée).
  const moved =
    settings.dns_auto === "ovh"
      ? t`<details><summary>Le domaine est passé chez Cloudflare ?</summary>${cloudflareFormHtml(settings)}</details>`
      : "";
  return t`<div class="bloc-ovh"><h3>DNS réglé par Jabba (${name})</h3>
    <p class="note">Jabba ne touche qu'à l'entrée ${prefix} : il la crée chez ${name} si elle manque et corrige son adresse si celle de ta box change (vérifié chaque heure). Le reste du domaine n'est jamais modifié.</p>
    <div class="actions"><button class="btn" type="button" id="dns-regler">Régler maintenant</button><button class="lien" type="button" id="${settings.dns_auto}-oublier">${settings.dns_auto === "cloudflare" ? t("Oublier le jeton") : t("Oublier la clé")}</button></div>${moved}</div>`;
}

function cloudflareFormHtml(settings) {
  const prefix = escapeHtml(settings.prefix || "cloud");
  const domain = escapeHtml(settings.domain);
  return t`<h3>Laisser Jabba régler le DNS (domaine chez Cloudflare)</h3>
    <ol class="note">
      <li>Ouvre la page de Cloudflare ci-dessous → <strong>Créer un jeton</strong> → modèle <strong>Modifier la zone DNS</strong>.</li>
      <li>Ressources de zone : <strong>Inclure → Zone spécifique → ${domain}</strong>. Ne change rien d'autre, et jamais la « clé API globale ».</li>
      <li><strong>Continuer vers le résumé → Créer le jeton</strong>, puis colle-le ici.</li>
    </ol>
    <p class="note">Garde l'entrée ${prefix} en « DNS uniquement » (nuage gris) : le certificat HTTPS se fait sur le serveur.</p>
    <p><a class="btn" href="${escapeHtml(settings.cloudflare_link)}" target="_blank" rel="noopener">Créer le jeton chez Cloudflare</a></p>
    <label class="champ"><span>Jeton Cloudflare</span><input type="password" id="cf-jeton" autocomplete="off"></label>
    <div class="actions"><button class="btn btn-p" type="button" id="cf-enregistrer">Enregistrer et régler le DNS</button></div>`;
}

function ovhFormHtml(settings) {
  const domain = escapeHtml(settings.domain);
  return t`<details><summary>Domaine chez OVH ?</summary>
    <ol class="note">
      <li>Ouvre la page d'OVH ci-dessous et connecte-toi : les droits sont déjà remplis, limités à la zone DNS de ${domain}. Dans « Validity », choisis <strong>Unlimited</strong>, puis <strong>Create keys</strong>.</li>
      <li>Recopie ici les trois codes affichés par OVH.</li>
    </ol>
    <p><a class="btn" href="${escapeHtml(settings.ovh_link)}" target="_blank" rel="noopener">Créer la clé chez OVH</a></p>
    <label class="champ"><span>Application key</span><input type="text" id="ovh-ak" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
    <label class="champ"><span>Application secret</span><input type="password" id="ovh-as" autocomplete="off"></label>
    <label class="champ"><span>Consumer key</span><input type="password" id="ovh-ck" autocomplete="off"></label>
    <div class="actions"><button class="btn" type="button" id="ovh-enregistrer">Enregistrer et régler le DNS</button></div></details>`;
}

export function bindDomain() {
  const detail = $("#domaine-detail");
  if (!detail) return;
  const isSuper = Boolean(state.admin && state.admin.rank === "super");
  const render = (settings) => {
    detail.innerHTML = settings.active ? checklistHtml(settings, isSuper) : formHtml(settings, isSuper);
    const enable = $("#domaine-activer");
    if (enable)
      enable.onclick = async () => {
        enable.disabled = true;
        try {
          const result = await api("/api/domaine", {
            method: "POST",
            json: { domain: $("#domaine-nom").value.trim(), prefix: $("#domaine-prefixe").value.trim() },
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
    const dnsCall = async (button, path, json, done) => {
      button.disabled = true;
      try {
        const result = await api(path, { method: "POST", json });
        render(result);
        toast(typeof done === "function" ? done(result) : done);
      } catch (error) {
        toast(error.message, true);
        button.disabled = false;
      }
    };
    const saveKeys = $("#ovh-enregistrer");
    if (saveKeys)
      saveKeys.onclick = () =>
        dnsCall(
          saveKeys,
          "/api/domaine/ovh",
          {
            application_key: $("#ovh-ak").value,
            application_secret: $("#ovh-as").value,
            consumer_key: $("#ovh-ck").value,
          },
          t("Clé enregistrée : l'entrée DNS est réglée chez OVH."),
        );
    const saveToken = $("#cf-enregistrer");
    if (saveToken)
      saveToken.onclick = () =>
        dnsCall(
          saveToken,
          "/api/domaine/cloudflare",
          { token: $("#cf-jeton").value },
          t("Jeton enregistré : l'entrée DNS est réglée chez Cloudflare."),
        );
    const syncNow = $("#dns-regler");
    if (syncNow) syncNow.onclick = () => dnsCall(syncNow, "/api/domaine/ovh/regler", {}, t("Entrée DNS vérifiée."));
    const forgetToken = $("#cloudflare-oublier");
    if (forgetToken)
      forgetToken.onclick = () =>
        dnsCall(forgetToken, "/api/domaine/cloudflare/oublier", {}, t("Jeton Cloudflare oublié."));
    const forgetKey = $("#ovh-oublier");
    if (forgetKey)
      forgetKey.onclick = () =>
        dnsCall(forgetKey, "/api/domaine/ovh/oublier", {}, (result) =>
          result.ovh_revoked
            ? t("Clé OVH oubliée et révoquée chez OVH.")
            : t("Clé OVH oubliée. Révoque-la aussi chez OVH (Mes codes)."),
        );
    const remove = $("#domaine-retirer");
    if (remove)
      remove.onclick = async () => {
        const ok = await confirmAction({
          title: t("Fermer l'accès par le domaine ?"),
          text: t(
            "Nextcloud ne sera plus joignable depuis Internet (il le reste par Tailscale). Pense aussi à retirer la redirection du port 443 dans la box.",
          ),
          confirmLabel: t("Fermer l'accès"),
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
