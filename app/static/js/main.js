// Point d'entrée de l'interface : charge chaque écran, puis lance la navigation.
//
// Rangement des fichiers :
//   core/        les outils communs (appels au serveur, navigation, session admin, mise en forme)
//   components/  les morceaux d'écran réutilisés (fenêtres de dialogue, visionneuse, lecteur vidéo, alertes)
//   pages/       un fichier par écran (Accueil, Établi, Fichiers, Disques, Invités, Comptes, Système…)
//
// Sommaire : la session (qui est connecté, mode invité, code d'entrée d'un QR code invité), puis le premier écran.
import "./pages/activity.js";
import "./pages/admins.js";
import "./pages/backups.js";
import "./pages/cloud.js";
import "./pages/disks.js";
import "./pages/files.js";
import "./pages/guest.js";
import "./pages/guests_admin.js";
import "./pages/help.js";
import "./pages/home.js";
import "./pages/system.js";
import "./pages/tools.js";
import "./pages/trash.js";
import "./pages/workshop.js";
import { toast } from "./components/dialogs.js";
import { showPasswordReset, chooseMyPassword, logOut } from "./core/admin_session.js";
import { api } from "./core/api.js";
import { $, escapeHtml } from "./core/dom.js";
import { pages } from "./core/pages.js";
import { decorateNavigation } from "./core/icons.js";
import { showError, router } from "./core/router.js";
import { state } from "./core/state.js";
import { rememberedServer } from "./pages/help.js";
import { t } from "./core/i18n.js";

window.addEventListener("hashchange", router);
decorateNavigation();

(async () => {
  try {
    state.me = await api("/api/moi");
    if (state.me.server) state.serverAccount = state.me.server.toLowerCase();
    if (state.me.admin && !state.me.admin.temporary)
      state.admin = {
        username: state.me.admin.username,
        rank: state.me.admin.rank,
        compte_serveur: state.me.admin.username === state.serverAccount,
      };
    document.body.classList.toggle("mode-invite", state.me.mode === "invite");
    $("#qui").innerHTML =
      state.me.mode === "invite"
        ? t`Mode invité · v${escapeHtml(state.me.version)}`
        : t`${escapeHtml(state.me.admin.username)} · ${state.me.admin.rank === "super" ? t("super admin") : "admin"}<br>v${escapeHtml(state.me.version)} · <a href="#" id="sortir">Se déconnecter</a>`;
    const logoutLink = $("#sortir");
    if (logoutLink)
      logoutLink.onclick = (event) => {
        event.preventDefault();
        logOut();
      };
    // QR code invité : http://<serveur>/?c=1234 → on entre tout seul, puis on retire le code de l'adresse.
    const code = new URLSearchParams(location.search).get("c");
    if (code && state.me.mode === "invite") {
      history.replaceState(null, "", location.pathname + "#/invite");
      try {
        await api("/api/invite/entrer", { method: "POST", json: { code } });
      } catch (error) {
        toast(error.message, true);
      }
    }
    rememberedServer.write("jabba-serveur", state.me.server || "");
    rememberedServer.write("jabba-dernier-contact", String(Date.now()));
  } catch (error) {
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
    if (location.hash === "#/aide") await pages.aide();
    else showError(error);
    return;
  }
  await router();
  if (state.me.password_reset) showPasswordReset();
  else if (state.me.admin && state.me.admin.temporary) chooseMyPassword(true).then((ok) => ok && location.reload());
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
})();
