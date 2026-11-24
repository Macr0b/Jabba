// « Installer sur cet appareil » : faire d'un outil une vraie appli (Dock du Mac, écran d'accueil de l'iPhone).
// Un site ne peut pas installer un autre site : Jabba ouvre l'outil et dit les deux clics à faire,
// selon le navigateur utilisé.
//
// Sommaire :
//   partagé : renderInstallAsApp
//   interne : browserSteps
import { escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";

function browserSteps() {
  const agent = navigator.userAgent;
  const iPhone = /iPhone|iPad|iPod/.test(agent) || (navigator.maxTouchPoints > 1 && /Macintosh/.test(agent));
  if (iPhone)
    return t(
      "Dans Safari : bouton Partager (le carré avec une flèche), puis « Sur l'écran d'accueil », puis « Ajouter ».",
    );
  if (/Android/.test(agent))
    return t("Dans Chrome : menu ⋮, puis « Ajouter à l'écran d'accueil » ou « Installer l'appli ».");
  if (/Edg\//.test(agent))
    return t("Dans Edge : menu …, puis « Applications » → « Installer ce site en tant qu'application ».");
  if (/Firefox\//.test(agent))
    return t("Firefox ne sait pas installer un site comme appli : ouvre Jabba dans Safari ou Chrome pour le faire.");
  if (/Chrome\//.test(agent))
    return t(
      "Dans Chrome : l'icône d'installation au bout de la barre d'adresse, ou menu ⋮ → « Caster, enregistrer et partager » → « Installer la page en tant qu'appli ».",
    );
  return t("Dans Safari : menu Fichier, puis « Ajouter au Dock », puis « Ajouter ».");
}

export function renderInstallAsApp(panel, item) {
  panel.innerHTML = `<p class="note">${escapeHtml(t`${item.name} devient une appli à part, avec sa propre fenêtre et son icône. Ouvre-le dans le navigateur, puis :`)}</p>
    <p><strong>${escapeHtml(browserSteps())}</strong></p>
    <div class="actions"><a class="btn btn-p" href="${escapeHtml(item.link)}" target="_blank" rel="noopener">${escapeHtml(t`Ouvrir ${item.name} dans le navigateur`)}</a></div>`;
}
