// Écran Corbeille : ce qui a été supprimé (disques et Nextcloud), à remettre en place ou à effacer.
//
// Sommaire :
//   partagé : pages.corbeille (écran)
import { chooseFromMenu, confirmAction, toast } from "../components/dialogs.js";
import { kindOf } from "../components/viewer.js";
import { api } from "../core/api.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatBytes, formatWhen } from "../core/format.js";
import { t } from "../core/i18n.js";
import { pages } from "../core/pages.js";
import { showError } from "../core/router.js";
import { loadingHtml } from "../core/tasks.js";

pages.corbeille = async () => {
  content.innerHTML = t`<div class="entete"><div><h1>Corbeille</h1></div></div>${loadingHtml(4)}`;
  const data = await api("/api/corbeille");
  const element = data.items;
  const days = (value) => Math.max(0, data.kept - Math.floor((Date.now() / 1000 - value) / 86400));
  content.innerHTML = t`
    <div class="entete"><div><h1>Corbeille</h1><p class="sous-titre">Ce que tu as supprimé, gardé ${data.kept} jours puis effacé tout seul.</p></div>
      <div class="actions"><a class="btn" href="#/fichiers">← Fichiers</a>${element.length ? t`<button class="btn btn-danger" id="vider">Vider la corbeille</button>` : ""}</div></div>
    <section class="carte tableau" aria-label="Éléments supprimés">
      <div class="ligne titres"><span>Nom</span><span class="col-l">Taille</span><span class="col-l">Supprimé</span><span></span></div>
      ${
        element.length
          ? element
              .map(
                (item, index) => t`
        <div class="ligne">
          <span class="nom">${item.folder ? '<span class="ic-dossier" aria-hidden="true"></span>' : `<span class="ic-fichier${kindOf(item.name) ? " " + kindOf(item.name) : ""}" aria-hidden="true"></span>`}
            <span>${escapeHtml(item.name)}<span class="detail-chemin">${item.origin ? t("Venait de ") + escapeHtml(item.origin) : t("Origine inconnue")} · encore ${days(item.deleted)} j</span></span></span>
          <span class="gris col-l">${formatBytes(item.size)}</span>
          <span class="gris col-l">${formatWhen(new Date(item.deleted * 1000).toISOString())}</span>
          <button class="btn-menu" data-i="${index}" aria-label="Actions pour ${escapeHtml(item.name)}">•••</button>
        </div>`,
              )
              .join("")
          : t`<div class="vide">La corbeille est vide.</div>`
      }
    </section>
    <p class="note">Restaurer remet l'élément à sa place. Effacer pour de bon demande le mot de passe du serveur.</p>`;
  const reload = () => pages.corbeille().catch(showError);
  const emptyButton = $("#vider");
  if (emptyButton)
    emptyButton.onclick = async () => {
      if (
        !(await confirmAction({
          title: t("Vider la corbeille ?"),
          text: t`Les ${element.length} éléments seront effacés pour de bon. Impossible de revenir en arrière.`,
          confirmLabel: t("Tout effacer"),
          danger: true,
        }))
      )
        return;
      try {
        await api("/api/corbeille/effacer", { method: "POST", json: { everything: true } });
        toast(t("Corbeille vidée"));
      } catch (error) {
        toast(error.message, true);
      }
      reload();
    };
  content.querySelectorAll(".btn-menu").forEach((node) =>
    node.addEventListener("click", async () => {
      const entry = element[+node.dataset.i];
      const choices = [];
      if (entry.origin)
        choices.push({
          text: t("Restaurer"),
          f: async () => {
            try {
              const data = await api("/api/corbeille/restaurer", {
                method: "POST",
                json: { id: entry.id, source: entry.source, origin: entry.origin },
              });
              toast(t`Restauré dans ${data.path}`);
            } catch (error) {
              toast(error.message, true);
            }
            reload();
          },
        });
      choices.push({
        text: t("Effacer pour de bon"),
        danger: true,
        f: async () => {
          if (
            !(await confirmAction({
              title: t`Effacer « ${entry.name} » pour de bon ?`,
              text: t("Impossible de revenir en arrière."),
              confirmLabel: t("Effacer"),
              danger: true,
            }))
          )
            return;
          try {
            await api("/api/corbeille/effacer", {
              method: "POST",
              json: { id: entry.id, source: entry.source, name: entry.name },
            });
            toast(t("Effacé"));
          } catch (error) {
            toast(error.message, true);
          }
          reload();
        },
      });
      const secondChoice = await chooseFromMenu(entry.name, choices);
      if (secondChoice) setTimeout(secondChoice.f, 50);
    }),
  );
};
