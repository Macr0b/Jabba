// Les identifiants d'un outil, gardés par Jabba (menu « … » → Identifiants, super admin).
// Compte créé par Jabba (AdGuard, Paperless, Time Machine) : à copier, il ne se change pas d'ici.
// Compte créé par toi dans l'outil (Jellyfin, Immich, Homebox…) : tu le notes une fois, Jabba le garde,
// et tu peux le modifier ou le faire oublier. Tout se fait dans la carte, sans fenêtre par-dessus.
// Les champs ne sont pas des champs « mot de passe » du navigateur : le trousseau ne propose pas de les
// enregistrer pour l'adresse de Jabba (ce qui écraserait un autre mot de passe).
//
// Sommaire :
//   partagé : renderToolLogin
//   interne : copy, showLogin, showForm, loginUrl
import { toast } from "./dialogs.js";
import { api } from "../core/api.js";
import { escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast(t("Copié"));
  } catch {
    toast(t("Copie impossible : sélectionne le texte à la main."), true);
  }
}

function loginUrl(item) {
  return `/api/etabli/${encodeURIComponent(item.id)}/identifiants`;
}

function showLogin(panel, item, login) {
  const hidden = "•".repeat(12);
  const intro = login.created_by_jabba
    ? t`Le compte créé par Jabba dans ${escapeHtml(item.name)}. Copie-le au moment de te connecter.`
    : t`Le compte que tu as noté pour ${escapeHtml(item.name)}. Jabba le garde pour toi.`;
  const actions = login.created_by_jabba
    ? ""
    : t`<div class="actions"><button class="btn" type="button" data-modifier>Modifier</button><button class="lien" type="button" data-oublier>Oublier</button></div>`;
  panel.innerHTML = t`<p class="note">${intro}</p>
    <dl class="identifiants-outil">
      <dt>Identifiant</dt><dd><code>${escapeHtml(login.username)}</code> <button class="lien" type="button" data-copier="username">Copier</button></dd>
      <dt>Mot de passe</dt><dd><code data-secret>${hidden}</code> <button class="lien" type="button" data-afficher>Afficher</button> <button class="lien" type="button" data-copier="password">Copier</button></dd>
    </dl>${actions}`;
  panel.querySelectorAll("[data-copier]").forEach((button) => {
    button.onclick = () => copy(login[button.dataset.copier]);
  });
  const secret = panel.querySelector("[data-secret]");
  panel.querySelector("[data-afficher]").onclick = (event) => {
    const shown = secret.textContent !== hidden;
    secret.textContent = shown ? hidden : login.password;
    event.target.textContent = shown ? t("Afficher") : t("Masquer");
  };
  const edit = panel.querySelector("[data-modifier]");
  if (edit) edit.onclick = () => showForm(panel, item, login);
  const forget = panel.querySelector("[data-oublier]");
  if (forget)
    forget.onclick = async () => {
      try {
        await api(loginUrl(item), { method: "DELETE" });
        toast(t`Jabba a oublié les identifiants de ${item.name}`);
        showForm(panel, item, null);
      } catch (error) {
        toast(error.message, true);
      }
    };
}

function showForm(panel, item, login) {
  panel.innerHTML = t`<p class="note">Note ici l'identifiant et le mot de passe que tu utilises dans ${escapeHtml(item.name)} : Jabba les garde pour toi, et seul le super admin peut les revoir.</p>
    <div class="formulaire-identifiants">
      <label class="champ"><span>Identifiant</span><input type="text" data-champ="identifiant" value="${escapeHtml((login && login.username) || "")}" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
      <label class="champ"><span>Mot de passe</span><input type="text" class="saisie-secrete" data-champ="mot_de_passe" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
    </div>
    <div class="actions"><button class="btn btn-p" type="button" data-enregistrer>Enregistrer</button>${login ? t`<button class="lien" type="button" data-annuler>Annuler</button>` : ""}</div>`;
  const field = (name) => panel.querySelector(`[data-champ="${name}"]`);
  panel.querySelector("[data-enregistrer]").onclick = async (event) => {
    event.target.disabled = true;
    try {
      const saved = await api(loginUrl(item), {
        method: "PUT",
        json: { username: field("identifiant").value, password: field("mot_de_passe").value },
      });
      toast(t`Identifiants de ${item.name} gardés par Jabba`);
      showLogin(panel, item, saved);
    } catch (error) {
      toast(error.message, true);
      event.target.disabled = false;
    }
  };
  const cancel = panel.querySelector("[data-annuler]");
  if (cancel) cancel.onclick = () => showLogin(panel, item, login);
}

export async function renderToolLogin(panel, item) {
  panel.innerHTML = `<p class="note">${escapeHtml(t("Chargement…"))}</p>`;
  try {
    showLogin(panel, item, await api(loginUrl(item)));
  } catch (error) {
    if (error.status === 404 && !item.jabba_credentials) return showForm(panel, item, null);
    panel.innerHTML = `<p class="note alerte-memoire">${escapeHtml(error.message)}</p>`;
  }
}
