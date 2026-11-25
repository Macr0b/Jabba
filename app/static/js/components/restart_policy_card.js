// Carte « Redémarrage après une mise à jour » (Réglages → Serveur) : seul à 4 h, ou à la main
// (conseillé si la machine a déjà bloqué au démarrage).
//
// Sommaire :
//   partagé : restartPolicyCard
import { api } from "../core/api.js";
import { $ } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { state } from "../core/state.js";
import { confirmAction, toast } from "./dialogs.js";

export async function restartPolicyCard() {
  const element = $("#redem");
  if (!element) return;
  const data = await api("/api/systeme/redemarrage");
  const superAdmin = state.admin && state.admin.rank === "super";
  const automatic = data.mode === "auto";
  element.innerHTML = t`<h2>Redémarrage après une mise à jour</h2>
    <p class="note" style="margin:8px 0">Certaines mises à jour du système ne prennent effet qu'au redémarrage. Le serveur peut le faire seul la nuit, ou te laisser le faire quand tu es devant : conseillé si la machine a déjà bloqué au démarrage.</p>
    <p style="margin:10px 0"><span class="pastille ${automatic ? "p-ok" : "p-gris"}">${automatic ? t("Seul, à 4 h") : t("À la main")}</span>
      <span class="note">${automatic ? t("Le serveur redémarre seul à 4 h quand une mise à jour le demande.") : t("Jabba te prévient ; tu redémarres avec le bouton « Redémarrer le serveur ».")}${data.chosen ? "" : t(" (réglage d'origine, pas encore choisi)")}</span></p>
    ${superAdmin ? `<div class="actions"><button class="btn" id="redem-changer">${automatic ? t("Passer à la main") : t("Redémarrer seul à 4 h")}</button></div>` : ""}`;
  const button = $("#redem-changer");
  if (button)
    button.onclick = async () => {
      const to = automatic ? "manuel" : "auto";
      const ok = await confirmAction(
        to === "auto"
          ? {
              title: t("Redémarrer seul à 4 h ?"),
              text: t(
                "Quand une mise à jour le demande, le serveur redémarrera seul à 4 h du matin. À éviter si la machine a déjà bloqué au démarrage : personne ne serait là pour la relancer.",
              ),
              confirmLabel: t("Redémarrer seul"),
            }
          : {
              title: t("Redémarrer à la main ?"),
              text: t(
                "Le serveur ne redémarrera plus seul. Jabba te préviendra quand une mise à jour attend : redémarre alors quand tu es devant. Les correctifs déjà installés restent actifs, sauf ceux du noyau qui attendent le redémarrage.",
              ),
              confirmLabel: t("À la main"),
            },
      );
      if (!ok) return;
      try {
        await api("/api/systeme/redemarrage", { method: "POST", json: { mode: to } });
        toast(to === "auto" ? t("Redémarrage automatique à 4 h") : t("Redémarrage à la main"));
      } catch (error) {
        toast(error.message, true);
      }
      restartPolicyCard().catch(() => {});
    };
}
