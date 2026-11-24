// Connexion admin : se connecter, confirmer son mot de passe, se déconnecter, mot de passe oublié.
//
// Sommaire :
//   partagé : confirmPassword, logOut, adminLogin, chooseMyPassword, showPasswordReset, showRecoveryCode
//   interne : readSavedIdentifier, saveIdentifier, rememberLogin, forgottenPassword, recoverPassword
import { confirmAction, dialogForm, dialogElement, toast } from "../components/dialogs.js";
import { secondLoginStep } from "../components/second_factor.js";
import { api, formBody } from "./api.js";
import { $, escapeHtml } from "./dom.js";
import { state } from "./state.js";
import { t } from "./i18n.js";

state.serverAccount = "serveur"; // nom du serveur = identifiant du super admin principal
state.admin = null; // {identifiant, rang, compte_serveur}
const readSavedIdentifier = () => {
  try {
    return localStorage.getItem("jabba-identifiant") || state.serverAccount;
  } catch (_) {
    return state.serverAccount;
  }
};
const saveIdentifier = (index) => {
  try {
    localStorage.setItem("jabba-identifiant", index);
  } catch (_) {
    /* rien */
  }
};

function rememberLogin(result) {
  const previous = state.admin;
  state.admin = {
    username: result.username,
    rank: result.rank,
    compte_serveur: result.username === state.serverAccount,
  };
  // On passait du mode invité au mode admin : on recharge l'interface complète.
  if (!previous && state.me && state.me.mode === "invite" && !result.changer) setTimeout(() => location.reload(), 300);
}

// Redemander le mot de passe avant une action sensible (déjà connecté).
export async function confirmPassword() {
  const answers = await formBody({
    title: t("Confirme ton mot de passe"),
    text: t("Cette action est sensible : ton mot de passe est redemandé (valable ensuite 10 minutes)."),
    fields: [
      {
        name: "username",
        cache: true,
        value: (state.admin && state.admin.username) || readSavedIdentifier(),
        autocomplete: "username",
      },
      { name: "password", type: "password", label: t("Mot de passe"), autocomplete: "current-password" },
    ],
    confirmLabel: t("Confirmer"),
  });
  if (!answers || answers === "secondary") return false;
  try {
    await api("/api/admin/confirmer", { method: "POST", json: { password: answers.password } }, true);
    return true;
  } catch (error) {
    toast(error.message, true);
    return false;
  }
}

export async function logOut() {
  try {
    await api("/api/admin/deconnexion", { method: "POST" }, true);
  } catch (_) {
    /* déjà déconnecté */
  }
  location.hash = "#/accueil";
  location.reload();
}

export async function adminLogin() {
  let identifier = readSavedIdentifier();
  for (let attempt = 0; attempt < 3; attempt++) {
    const answers = await formBody({
      title: t("Connexion admin"),
      text: t`Identifiant « ${state.serverAccount} » = compte du serveur, avec son mot de passe. La connexion reste mémorisée 30 jours sur cet appareil.`,
      fields: [
        { name: "username", label: t("Identifiant"), value: identifier, autocomplete: "username" },
        { name: "password", type: "password", label: t("Mot de passe"), autocomplete: "current-password" },
      ],
      confirmLabel: t("Continuer"),
      secondary: t("Mot de passe oublié ?"),
    });
    if (answers === null) return false;
    if (answers === "secondary") {
      await forgottenPassword(identifier);
      continue;
    }
    identifier = answers.username.trim().toLowerCase();
    try {
      const data = await api(
        "/api/admin/connexion",
        { method: "POST", json: { username: identifier, password: answers.password } },
        true,
      );
      saveIdentifier(identifier);
      if (data.step) {
        // Double vérification : Face ID, appli d'authentification ou code de secours.
        const opened = await secondLoginStep(data);
        if (!opened) return false;
        rememberLogin(opened);
        if (opened.changer) return chooseMyPassword(true);
        return true;
      }
      rememberLogin(data);
      if (data.changer) return chooseMyPassword(true);
      return true;
    } catch (error) {
      toast(error.message, true);
    }
  }
  return false;
}

// Un admin (compte de l'appli) choisit son mot de passe : obligatoire après un mot de passe provisoire.
export async function chooseMyPassword(force = false) {
  const answers = await formBody({
    title: force ? t("Choisis ton mot de passe") : t("Changer mon mot de passe"),
    text: force
      ? t(
          "Ton mot de passe actuel est provisoire. Choisis le tien (15 caractères minimum, une phrase de quelques mots convient) : l'app Mots de passe proposera de l'enregistrer.",
        )
      : "",
    fields: [
      {
        name: "username",
        cache: true,
        value: (state.admin && state.admin.username) || readSavedIdentifier(),
        autocomplete: "username",
      },
      ...(force
        ? []
        : [{ name: "old", type: "password", label: t("Mot de passe actuel"), autocomplete: "current-password" }]),
      { name: "new", type: "password", label: t("Nouveau mot de passe"), autocomplete: "new-password" },
      {
        name: "confirmation",
        type: "password",
        label: t("Nouveau mot de passe, encore une fois"),
        autocomplete: "new-password",
      },
    ],
    confirmLabel: t("Enregistrer"),
  });
  if (!answers || answers === "secondary") return false;
  if (answers.new !== answers.confirmation) {
    toast(t("Les deux mots de passe ne sont pas identiques."), true);
    return chooseMyPassword(force);
  }
  try {
    const data = await api(
      "/api/comptes-moi/mot-de-passe",
      { method: "POST", json: { old: answers.old || "", new: answers.new } },
      true,
    );
    rememberLogin(data);
    toast(t("Mot de passe enregistré"));
    return true;
  } catch (error) {
    toast(error.message, true);
    return false;
  }
}

async function forgottenPassword(identifier) {
  const answers = await formBody({
    title: t("Mot de passe oublié"),
    text: t`Pour le compte du serveur (« ${state.serverAccount} »), tu utiliseras ton code de secours. Pour un autre compte, la demande part chez le super admin.`,
    fields: [{ name: "username", label: t("Ton identifiant"), value: identifier, autocomplete: "username" }],
    confirmLabel: t("Continuer"),
  });
  if (!answers || answers === "secondary") return;
  try {
    const data = await api("/api/comptes-oubli", { method: "POST", json: { username: answers.username } }, true);
    if (data.server) return recoverPassword();
    await confirmAction({
      title: t("Demande envoyée"),
      text: t(
        "Si cet identifiant existe et qu'il est relié à ton compte Tailscale, le super admin est prévenu. Dès qu'il accepte, tu reçois une alerte et ton mot de passe provisoire s'affiche ici, sur cet appareil. Sinon, rien n'est envoyé : demande directement à un super admin de te recréer un compte.",
      ),
      confirmLabel: "OK",
    });
  } catch (error) {
    toast(error.message, true);
  }
}

// Mot de passe provisoire remis à la personne concernée, une seule fois.
export async function showPasswordReset() {
  let result;
  try {
    result = await api("/api/comptes-remise", { method: "POST" });
  } catch (_) {
    return;
  }
  await new Promise((resolve) => {
    dialogForm.innerHTML = t`<h2>Ton mot de passe provisoire</h2>
      <p>Identifiant : <strong>${escapeHtml(result.username)}</strong>. Ce mot de passe ne sera <strong>plus affiché</strong> : il sert une fois, dans les 24 h, puis tu choisis le tien.</p>
      <p class="code-secours">${escapeHtml(result.password)}</p>
      <div class="actions"><button class="btn" type="button" id="copier-code">Copier</button><button class="btn btn-p" value="ok" type="submit">Me connecter</button></div>`;
    dialogElement.onclose = () => resolve();
    dialogElement.returnValue = "";
    dialogElement.showModal();
    $("#copier-code").onclick = async () => {
      try {
        await navigator.clipboard.writeText(result.password);
        toast(t("Copié"));
      } catch (_) {
        toast(t("Copie impossible : sélectionne-le à la main."), true);
      }
    };
  });
  saveIdentifier(result.username);
  await adminLogin();
}

export async function showRecoveryCode(code) {
  await new Promise((resolve) => {
    dialogForm.innerHTML = t`<h2>Ton code de secours</h2>
      <p>Il sert si tu oublies le mot de passe du serveur. Il ne sera <strong>plus jamais affiché</strong> : enregistre-le maintenant dans l'app Mots de passe (dans les notes de l'entrée « ${escapeHtml(state.serverAccount)} ») et note-le sur papier.</p>
      <p class="code-secours">${escapeHtml(code)}</p>
      <div class="actions"><button class="btn" type="button" id="copier-code">Copier</button><button class="btn btn-p" value="ok" type="submit">C'est noté</button></div>`;
    dialogElement.onclose = () => resolve();
    dialogElement.showModal();
    $("#copier-code").onclick = async () => {
      try {
        await navigator.clipboard.writeText(code);
        toast(t("Code copié"));
      } catch (_) {
        toast(t("Copie impossible : sélectionne le code à la main."), true);
      }
    };
  });
}

async function recoverPassword() {
  const answers = await formBody({
    title: t("Mot de passe oublié"),
    text: t("Tape ton code de secours (les 6 mots), puis choisis un nouveau mot de passe pour le serveur."),
    fields: [
      { name: "code", label: t("Code de secours") },
      { name: "username", cache: true, value: state.serverAccount, autocomplete: "username" },
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
    confirmLabel: t("Changer le mot de passe"),
  });
  if (!answers || answers === "secondary") return;
  if (answers.new !== answers.confirmation) return toast(t("Les deux mots de passe ne sont pas identiques."), true);
  try {
    const result = await api(
      "/api/admin/secours",
      { method: "POST", json: { code: answers.code, new: answers.new } },
      true,
    );
    toast(t("Mot de passe changé"));
    await showRecoveryCode(result.code);
  } catch (error) {
    toast(error.message, true);
  }
}
