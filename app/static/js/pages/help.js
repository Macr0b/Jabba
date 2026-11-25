// Écran Aide, et l'écran « serveur injoignable » (avec les premiers gestes).
//
// Sommaire :
//   partagé : rememberedServer, retryTimer, lastFailure, showUnreachable, pages.aide (écran)
//   interne : HELP_TOPICS
import { toast } from "../components/dialogs.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatWhen } from "../core/format.js";
import { pages } from "../core/pages.js";
import { t } from "../core/i18n.js";

// ------------------------------------------------------------ serveur injoignable et aide
export const rememberedServer = {
  lire: (key) => {
    try {
      return localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  },
  write: (key, value) => {
    try {
      localStorage.setItem(key, value);
    } catch (_) {
      /* rien */
    }
  },
};
export let retryTimer = null;
export let lastFailure = "reseau";

export function showUnreachable(type) {
  lastFailure = type;
  const name = rememberedServer.lire("jabba-serveur") || t("le serveur");
  const latest = +rememberedServer.lire("jabba-dernier-contact") || 0;
  const offline = navigator.onLine === false;
  const tracks = offline
    ? [
        [
          t("Cet appareil n'a pas de connexion"),
          t("Vérifie le Wi-Fi ou les données mobiles de cet appareil, puis réessaie."),
        ],
      ]
    : type === "appli"
      ? [
          [
            t("Une mise à jour est peut-être en cours"),
            t("Après « Installer », l'appli redémarre : attends 2 minutes, la page se rouvrira toute seule."),
          ],
          [
            t("L'appli Jabba s'est arrêtée"),
            t`${escapeHtml(name)} est allumé et joignable, mais l'appli ne répond pas. Redémarre le serveur (appui court sur le bouton d'alimentation, puis rallume-le) : l'appli repart toute seule.`,
          ],
        ]
      : [
          [
            t("Tailscale est-il connecté sur cet appareil ?"),
            t("Ouvre l'appli Tailscale : elle doit indiquer « Connected ». Sinon, active-la."),
          ],
          [
            t("Un VPN est-il actif ?"),
            t("NordVPN ou un autre VPN peut bloquer Tailscale et le réseau local. Coupe-le, puis réessaie."),
          ],
          [
            t`${escapeHtml(name[0].toUpperCase() + name.slice(1))} est-il allumé ?`,
            t(
              "Après une coupure de courant, il peut rester éteint : appuie sur son bouton d'alimentation et attends 2 minutes.",
            ),
          ],
          [
            t("Son câble réseau est-il branché ?"),
            t(
              "Vérifie le câble entre le serveur et la Freebox, et que la Freebox fonctionne (Internet marche sur tes autres appareils ?).",
            ),
          ],
        ];
  document.body.classList.add("injoignable");
  content.innerHTML = t`
    <div class="entete"><div><h1>${type === "appli" ? t("Jabba ne répond pas") : t`${escapeHtml(name[0].toUpperCase() + name.slice(1))} est injoignable`}</h1>
      <p class="sous-titre">${latest ? t("Dernier contact réussi ") + formatWhen(new Date(latest).toISOString()).toLowerCase() + ". " : ""}Nouvel essai automatique toutes les 10 secondes.</p></div>
      <div class="actions"><a class="btn" href="#/aide">Aide</a><button class="btn btn-p" id="reessayer">Réessayer</button></div></div>
    <section class="carte pad pistes">
      <h2>Pistes, dans l'ordre</h2>
      <ol>${tracks.map(([value, data]) => `<li><strong>${value}</strong><span>${data}</span></li>`).join("")}</ol>
    </section>
    <p class="note">Rien n'est perdu : les fichiers restent sur le serveur. Dès qu'il répond, cette page se recharge toute seule.</p>`;
  const attempt = async () => {
    try {
      const response = await fetch("/api/moi", { credentials: "same-origin", cache: "no-store" });
      if (response.ok) location.reload();
    } catch (_) {
      /* toujours injoignable */
    }
  };
  $("#reessayer").onclick = () => {
    toast(t("Nouvel essai…"));
    attempt();
  };
  clearInterval(retryTimer);
  retryTimer = setInterval(attempt, 10000);
}

const HELP_TOPICS = [
  [
    t("Se connecter sans son téléphone (double vérification)"),
    [
      t(
        "À la connexion, touche « Utiliser un code de secours » et tape l'un des 10 codes reçus à l'activation (chaque code sert une fois).",
      ),
      t(
        "Plus de codes ni de téléphone : un autre super admin ouvre Système, carte Double vérification, « Téléphone perdu (autre compte) ».",
      ),
      t("Une fois reconnecté, ajoute ton nouveau téléphone et crée de nouveaux codes de secours."),
    ],
  ],
  [
    t("Plan de reprise : le serveur est mort, tout réinstaller"),
    [
      t(
        "À faire dès aujourd'hui : range la phrase de passe des sauvegardes (montrée une seule fois à leur mise en route) et le code de secours du serveur dans l'app Mots de passe, pas seulement sur le serveur. Sans elle, la sauvegarde est illisible.",
      ),
      t(
        "Sur une machine neuve : installe Debian, puis suis la méthode d'installation de Jabba (installer.sh). Choisis le même nom de serveur.",
      ),
      t(
        "Branche le disque des sauvegardes, puis, depuis le Terminal du Mac, connecte-toi au serveur en SSH (ssh ton-compte@nom-du-serveur) et liste les copies : restic -r /chemin/du/disque/.sauvegardes-jabba snapshots (la phrase de passe est demandée).",
      ),
      t(
        "Remets les fichiers : restic -r … restore latest --target / --include /srv/data. Fais de même pour /opt/stacks (les outils et leurs réglages). /etc n'est pas à remettre en entier : on y reprend seulement ce dont on a besoin.",
      ),
      t(
        "Nextcloud : la base de données est dans /opt/stacks/nextcloud/sauvegarde-base.sql ; réimporte-la dans le conteneur nextcloud-db avant de relancer Nextcloud depuis l'Établi.",
      ),
      t(
        "Fais ce test une fois, à blanc, sur un autre ordinateur : c'est le seul moyen d'être sûr que la sauvegarde sert vraiment.",
      ),
    ],
  ],
  [
    t("Jabba ne s'ouvre pas ou dit « injoignable »"),
    [
      t("Ouvre l'appli Tailscale sur cet appareil : elle doit indiquer « Connected »."),
      t("Coupe tout VPN (NordVPN…) : il peut bloquer Tailscale et le réseau local."),
      t(
        "Vérifie que le serveur est allumé (après une coupure de courant, rallume-le) et que son câble réseau va bien à la Freebox.",
      ),
      t("Essaie depuis un autre appareil : si ça marche ailleurs, le souci vient de cet appareil."),
      t("Juste après une mise à jour de l'appli, attends 2 minutes."),
    ],
  ],
  [
    t("Partager un dossier avec quelqu'un"),
    [
      t("La personne a besoin d'un compte Nextcloud (Comptes) : elle verra le dossier dans son Nextcloud."),
      t(
        "Fichiers, bouton ••• à droite du dossier, « Partager avec… » : choisis un rang (il compte aussi pour les rangs au-dessus), « Tous les membres » ou un groupe, puis « Voir seulement » ou « Voir et modifier ».",
      ),
      t(
        "Ça marche aussi pour un dossier d'un disque (R2-D2…) : la personne ne voit que ce dossier, jamais le reste du disque.",
      ),
      t(
        "Pour retirer l'accès : Comptes, onglet Dossiers et rangs, « Dossiers partagés », Arrêter. Les fichiers ne bougent pas.",
      ),
    ],
  ],
  [
    t("Un disque branché n'apparaît pas"),
    [
      t("Attends 15 secondes, puis Disques → Actualiser."),
      t(
        "Formats lisibles : exFAT, FAT, NTFS, ext4. Un disque Mac (APFS) est signalé comme illisible : reformate-le en exFAT avec l'Utilitaire de disque.",
      ),
      t(
        "Le disque fait des petits clics et n'apparaît pas : il manque de courant. Branche-le directement sur le serveur, ou sur un hub alimenté (avec son propre bloc secteur) : un hub sans alimentation ne suffit pas pour un disque 2,5\".",
      ),
      t("Si tu avais cliqué « Oublier ce disque », remets-le en ajout automatique depuis Disques."),
    ],
  ],
  [
    t("Un dossier de disque paraît vide dans Fichiers"),
    [
      t(
        "Juste après le branchement, Nextcloud relit le disque : quelques minutes pour un gros disque. Actualise ensuite.",
      ),
      t("Vérifie dans Disques que le disque est bien affiché avec son espace utilisé."),
    ],
  ],
  [
    t("Mot de passe oublié"),
    [
      t("Compte du serveur : « Mot de passe oublié ? » à la connexion, puis le code de secours (6 mots)."),
      t(
        "Autre compte admin : « Mot de passe oublié ? » envoie une demande au super admin ; une fois acceptée, un mot de passe provisoire s'affiche sur ton appareil.",
      ),
    ],
  ],
  [
    t("Les alertes n'arrivent pas"),
    [
      t(
        "Sur iPhone, Jabba doit être installée sur l'écran d'accueil (Partager → Sur l'écran d'accueil) pour recevoir des alertes.",
      ),
      t("Réglages → Alertes : active les alertes sur cet appareil et vérifie les interrupteurs par type."),
      t("Réglages de l'appareil → Notifications → Jabba : elles doivent être autorisées."),
    ],
  ],
  [
    t("Les invités n'arrivent pas sur la page Wi-Fi"),
    [
      t("Invités : l'accès doit être ouvert."),
      t("L'invité doit être sur le même Wi-Fi que le serveur, sans VPN."),
      t("Donne-lui le QR code ; si tu as changé le code, l'ancien ne marche plus."),
    ],
  ],
  [
    t("Le serveur ne revient pas après un redémarrage"),
    [
      t(
        "Attends 5 minutes : un disque lent ou une vérification peuvent retarder le démarrage. Le serveur ne réapparaît dans Tailscale qu'à la fin.",
      ),
      t(
        "Écran noir et rien ne bouge : débranche les disques et adaptateurs USB, puis éteins (appui long sur le bouton). Si le portable ne se rallume plus, utilise le petit trou de secours (bouton Novo, sur le côté) avec un trombone : choisis « Normal Startup ».",
      ),
      t(
        "Réglages du BIOS conseillés pour un portable serveur (touche F2 ou bouton Novo → BIOS Setup) : démarrage USB désactivé, Fast Boot désactivé, démarrage réseau (PXE) désactivé, disque interne en premier. F10 pour enregistrer.",
      ),
      t(
        "Si la machine bloque parfois au démarrage : Réglages → Serveur → Redémarrage après une mise à jour → « À la main ». Le serveur ne redémarrera plus seul la nuit ; Jabba te prévient et tu redémarres quand tu es devant.",
      ),
      t("Si les blocages continuent malgré tout : mise à jour du BIOS (site du fabricant), ou changer de machine."),
    ],
  ],
  [
    t("Jellyfin, Nextcloud ou un autre outil ne s'ouvre pas"),
    [
      t("Établi : l'interrupteur de l'outil doit être activé."),
      t("Utilise « Redémarrer » sur l'outil, puis attends une minute."),
      t("Un outil qui affiche « illisible » ou « arrêté » est aussi signalé dans le Journal (Système)."),
    ],
  ],
];

pages.aide = async () => {
  content.innerHTML = t`
    <div class="entete"><div><h1>Aide</h1><p class="sous-titre">Pistes et solutions aux soucis les plus courants</p></div>
      <div class="actions"><button class="btn" id="retour-aide">← Retour</button></div></div>
    <section class="carte aide">${HELP_TOPICS.map(
      ([title, tracks], index) => `
      <details${index === 0 && document.body.classList.contains("injoignable") ? " open" : ""}><summary>${escapeHtml(title)}</summary>
        <ul>${tracks.map((value) => `<li>${escapeHtml(value)}</li>`).join("")}</ul></details>`,
    ).join("")}
    </section>`;
  $("#retour-aide").onclick = () => (history.length > 1 ? history.back() : (location.hash = "#/accueil"));
};
