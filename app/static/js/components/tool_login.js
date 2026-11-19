// Le compte que Jabba a créé dans un outil (AdGuard, Paperless, Time Machine) : à copier pour s'y connecter.
// Le mot de passe a été tiré au sort à l'activation : rien à retenir, il se recopie d'ici.
//
// Sommaire :
//   partagé : renderToolLogin
//   interne : copy
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

export async function renderToolLogin(panel, item) {
  panel.innerHTML = `<p class="note">${escapeHtml(t("Chargement…"))}</p>`;
  let login;
  try {
    login = await api(`/api/etabli/${encodeURIComponent(item.id)}/identifiants`);
  } catch (error) {
    panel.innerHTML = `<p class="note alerte-memoire">${escapeHtml(error.message)}</p>`;
    return;
  }
  const hidden = "•".repeat(12);
  panel.innerHTML = t`<p class="note">Le compte créé par Jabba dans ${escapeHtml(item.nom)}. Enregistre-le dans ton gestionnaire au moment de te connecter.</p>
    <dl class="identifiants-outil">
      <dt>Identifiant</dt><dd><code>${escapeHtml(login.identifiant)}</code> <button class="lien" type="button" data-copier="identifiant">Copier</button></dd>
      <dt>Mot de passe</dt><dd><code data-secret>${hidden}</code> <button class="lien" type="button" data-afficher>Afficher</button> <button class="lien" type="button" data-copier="mot_de_passe">Copier</button></dd>
    </dl>`;
  panel.querySelectorAll("[data-copier]").forEach((button) => {
    button.onclick = () => copy(login[button.dataset.copier]);
  });
  const secret = panel.querySelector("[data-secret]");
  panel.querySelector("[data-afficher]").onclick = (event) => {
    const shown = secret.textContent !== hidden;
    secret.textContent = shown ? hidden : login.mot_de_passe;
    event.target.textContent = shown ? t("Afficher") : t("Masquer");
  };
}
