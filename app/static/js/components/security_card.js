// Carte « Sécurité » (Réglages → Sécurité) : qui est connecté, le mot de passe du serveur (ou le sien),
// le code de secours, et le lien vers les admins de Jabba.
//
// Sommaire :
//   partagé : securityCardHtml, bindSecurityCard
import { chooseMyPassword, showRecoveryCode } from "../core/admin_session.js";
import { api, formBody } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { state } from "../core/state.js";
import { confirmAction, toast } from "./dialogs.js";

/** `entry` : la réponse de /api/admin/etat. */
export function securityCardHtml(entry) {
  return t`<section class="carte pad"><div class="entete-carte"><h2>Sécurité</h2><a class="btn btn-petit" href="#/utilisateurs">Admins de Jabba</a></div>
    <p class="note" id="connecte-admin">${entry.connected ? t`Connecté en admin : <strong>${escapeHtml(entry.connected.username)}</strong> · ${entry.connected.rank === "super" ? t("super admin") : "admin"}` : t("Pas connecté en admin.")}</p>
    <p class="note" style="margin:8px 0 14px">Le mot de passe du serveur protège les actions sensibles : activer un outil, mettre à jour, redémarrer, régler ou oublier un disque, supprimer un dossier entier, effacer la corbeille.</p>
    ${entry.code_exists ? "" : t`<div class="bandeau" style="margin-bottom:12px"><span>Aucun code de secours : crée-le maintenant, il sert si tu oublies le mot de passe.</span></div>`}
    <div class="actions"><button class="btn" id="changer-mdp">${entry.connected && entry.connected.username !== entry.server ? t("Changer mon mot de passe") : t("Changer le mot de passe du serveur")}</button>
    <button class="btn" id="code-secours">${entry.code_exists ? t("Nouveau code de secours") : t("Créer le code de secours")}</button></div>
  </section>`;
}

/** `reload` : réaffiche l'écran (après un nouveau code de secours). */
export function bindSecurityCard(entry, reload) {
  $("#changer-mdp").onclick = async () => {
    if (entry.connected && entry.connected.username !== entry.server) {
      await chooseMyPassword(false);
      return;
    }
    const answers = await formBody({
      title: t("Changer le mot de passe du serveur"),
      text: t("C'est aussi le mot de passe de sudo quand tu te connectes en SSH."),
      fields: [
        { name: "username", cache: true, value: state.serverAccount, autocomplete: "username" },
        { name: "old", type: "password", label: t("Mot de passe actuel"), autocomplete: "current-password" },
        {
          name: "new",
          type: "password",
          label: t("Nouveau mot de passe (15 caractères minimum, une phrase de quelques mots convient)"),
          autocomplete: "new-password",
        },
        {
          name: "confirmation",
          type: "password",
          label: t("Nouveau mot de passe, encore une fois"),
          autocomplete: "new-password",
        },
      ],
      confirmLabel: t("Changer"),
    });
    if (!answers || answers === "secondary") return;
    if (answers.new !== answers.confirmation) return toast(t("Les deux mots de passe ne sont pas identiques."), true);
    try {
      await api("/api/admin/mot-de-passe", {
        method: "POST",
        json: { old: answers.old, new: answers.new },
      });
      toast(t("Mot de passe changé : reconnecte-toi"));
      setTimeout(() => location.reload(), 1500);
    } catch (error) {
      toast(error.message, true);
    }
  };
  $("#code-secours").onclick = async () => {
    if (
      entry.code_exists &&
      !(await confirmAction({
        title: t("Créer un nouveau code de secours ?"),
        text: t("L'ancien code ne marchera plus."),
        confirmLabel: t("Créer"),
      }))
    )
      return;
    try {
      const result = await api("/api/admin/code", { method: "POST" });
      await showRecoveryCode(result.code);
      reload();
    } catch (error) {
      toast(error.message, true);
    }
  };
}
