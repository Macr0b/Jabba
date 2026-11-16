// Écran Aide, et l'écran « serveur injoignable » (avec les premiers gestes).
//
// Sommaire :
//   partagé : rememberedServer, retryTimer, lastFailure, showUnreachable, pages.aide (écran)
//   interne : HELP_TOPICS
import { toast } from "../components/dialogs.js";
import { $, content, escapeHtml } from "../core/dom.js";
import { formatWhen } from "../core/format.js";
import { pages } from "../core/pages.js";

// ------------------------------------------------------------ serveur injoignable et aide
export const rememberedServer = {
  lire: (key) => {
    try {
      return localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  },
  ecrire: (key, value) => {
    try {
      localStorage.setItem(key, value);
    } catch (_) {
      /* rien */
    }
  },
};
export let retryTimer = null,
  lastFailure = "reseau";

export function showUnreachable(type) {
  lastFailure = type;
  const name = rememberedServer.lire("jabba-serveur") || "le serveur";
  const latest = +rememberedServer.lire("jabba-dernier-contact") || 0;
  const offline = navigator.onLine === false;
  const tracks = offline
    ? [["Cet appareil n'a pas de connexion", "Vérifie le Wi-Fi ou les données mobiles de cet appareil, puis réessaie."]]
    : type === "appli"
      ? [
          [
            "Une mise à jour est peut-être en cours",
            "Après « Installer », l'appli redémarre : attends 2 minutes, la page se rouvrira toute seule.",
          ],
          [
            "L'appli Jabba s'est arrêtée",
            `${escapeHtml(name)} est allumé et joignable, mais l'appli ne répond pas. Redémarre le serveur (appui court sur le bouton d'alimentation, puis rallume-le) : l'appli repart toute seule.`,
          ],
        ]
      : [
          [
            "Tailscale est-il connecté sur cet appareil ?",
            "Ouvre l'appli Tailscale : elle doit indiquer « Connected ». Sinon, active-la.",
          ],
          [
            "Un VPN est-il actif ?",
            "NordVPN ou un autre VPN peut bloquer Tailscale et le réseau local. Coupe-le, puis réessaie.",
          ],
          [
            `${escapeHtml(name[0].toUpperCase() + name.slice(1))} est-il allumé ?`,
            "Après une coupure de courant, il peut rester éteint : appuie sur son bouton d'alimentation et attends 2 minutes.",
          ],
          [
            "Son câble réseau est-il branché ?",
            "Vérifie le câble entre le serveur et la Freebox, et que la Freebox fonctionne (Internet marche sur tes autres appareils ?).",
          ],
        ];
  document.body.classList.add("injoignable");
  content.innerHTML = `
    <div class="entete"><div><h1>${type === "appli" ? "Jabba ne répond pas" : `${escapeHtml(name[0].toUpperCase() + name.slice(1))} est injoignable`}</h1>
      <p class="sous-titre">${latest ? "Dernier contact réussi " + formatWhen(new Date(latest).toISOString()).toLowerCase() + ". " : ""}Nouvel essai automatique toutes les 10 secondes.</p></div>
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
    toast("Nouvel essai…");
    attempt();
  };
  clearInterval(retryTimer);
  retryTimer = setInterval(attempt, 10000);
}

const HELP_TOPICS = [
  [
    "Se connecter sans son téléphone (double vérification)",
    [
      "À la connexion, touche « Utiliser un code de secours » et tape l'un des 10 codes reçus à l'activation (chaque code sert une fois).",
      "Plus de codes ni de téléphone : un autre super admin ouvre Système, carte Double vérification, « Téléphone perdu (autre compte) ».",
      "Une fois reconnecté, ajoute ton nouveau téléphone et crée de nouveaux codes de secours.",
    ],
  ],
  [
    "Plan de reprise : le serveur est mort, tout réinstaller",
    [
      "À faire dès aujourd'hui : range la phrase de passe des sauvegardes (montrée une seule fois à leur mise en route) et le code de secours du serveur dans l'app Mots de passe, pas seulement sur le serveur. Sans elle, la sauvegarde est illisible.",
      "Sur une machine neuve : installe Debian, puis suis la méthode d'installation de Jabba (installer.sh). Choisis le même nom de serveur.",
      "Branche le disque des sauvegardes, puis, depuis le Terminal du Mac, connecte-toi au serveur en SSH (ssh ton-compte@nom-du-serveur) et liste les copies : restic -r /chemin/du/disque/.sauvegardes-jabba snapshots (la phrase de passe est demandée).",
      "Remets les fichiers : restic -r … restore latest --target / --include /srv/data. Fais de même pour /opt/stacks (les outils et leurs réglages). /etc n'est pas à remettre en entier : on y reprend seulement ce dont on a besoin.",
      "Nextcloud : la base de données est dans /opt/stacks/nextcloud/sauvegarde-base.sql ; réimporte-la dans le conteneur nextcloud-db avant de relancer Nextcloud depuis l'Établi.",
      "Fais ce test une fois, à blanc, sur un autre ordinateur : c'est le seul moyen d'être sûr que la sauvegarde sert vraiment.",
    ],
  ],
  [
    "Jabba ne s'ouvre pas ou dit « injoignable »",
    [
      "Ouvre l'appli Tailscale sur cet appareil : elle doit indiquer « Connected ».",
      "Coupe tout VPN (NordVPN…) : il peut bloquer Tailscale et le réseau local.",
      "Vérifie que le serveur est allumé (après une coupure de courant, rallume-le) et que son câble réseau va bien à la Freebox.",
      "Essaie depuis un autre appareil : si ça marche ailleurs, le souci vient de cet appareil.",
      "Juste après une mise à jour de l'appli, attends 2 minutes.",
    ],
  ],
  [
    "Partager un dossier avec quelqu'un",
    [
      "La personne a besoin d'un compte Nextcloud (Comptes) : elle verra le dossier dans son Nextcloud.",
      "Fichiers, bouton ••• à droite du dossier, « Partager avec… » : choisis un rang (il compte aussi pour les rangs au-dessus), « Tous les membres » ou un groupe, puis « Voir seulement » ou « Voir et modifier ».",
      "Ça marche aussi pour un dossier d'un disque (R2-D2…) : la personne ne voit que ce dossier, jamais le reste du disque.",
      "Pour retirer l'accès : Comptes, onglet Dossiers et rangs, « Dossiers partagés », Arrêter. Les fichiers ne bougent pas.",
    ],
  ],
  [
    "Un disque branché n'apparaît pas",
    [
      "Attends 15 secondes, puis Disques → Actualiser.",
      "Formats lisibles : exFAT, FAT, NTFS, ext4. Un disque Mac (APFS) est signalé comme illisible : reformate-le en exFAT avec l'Utilitaire de disque.",
      "Le disque fait des petits clics et n'apparaît pas : il manque de courant. Branche-le directement sur le serveur, ou sur un hub alimenté (avec son propre bloc secteur) : un hub sans alimentation ne suffit pas pour un disque 2,5\".",
      "Si tu avais cliqué « Oublier ce disque », remets-le en ajout automatique depuis Disques.",
    ],
  ],
  [
    "Un dossier de disque paraît vide dans Fichiers",
    [
      "Juste après le branchement, Nextcloud relit le disque : quelques minutes pour un gros disque. Actualise ensuite.",
      "Vérifie dans Disques que le disque est bien affiché avec son espace utilisé.",
    ],
  ],
  [
    "Mot de passe oublié",
    [
      "Compte du serveur : « Mot de passe oublié ? » à la connexion, puis le code de secours (6 mots).",
      "Autre compte admin : « Mot de passe oublié ? » envoie une demande au super admin ; une fois acceptée, un mot de passe provisoire s'affiche sur ton appareil.",
    ],
  ],
  [
    "Les alertes n'arrivent pas",
    [
      "Sur iPhone, Jabba doit être installée sur l'écran d'accueil (Partager → Sur l'écran d'accueil) pour recevoir des alertes.",
      "Système → Alertes : active les alertes sur cet appareil et vérifie les interrupteurs par type.",
      "Réglages de l'appareil → Notifications → Jabba : elles doivent être autorisées.",
    ],
  ],
  [
    "Les invités n'arrivent pas sur la page Wi-Fi",
    [
      "Invités : l'accès doit être ouvert.",
      "L'invité doit être sur le même Wi-Fi que le serveur, sans VPN.",
      "Donne-lui le QR code ; si tu as changé le code, l'ancien ne marche plus.",
    ],
  ],
  [
    "Le serveur ne revient pas après un redémarrage",
    [
      "Attends 5 minutes : un disque lent ou une vérification peuvent retarder le démarrage. Le serveur ne réapparaît dans Tailscale qu'à la fin.",
      "Écran noir et rien ne bouge : débranche les disques et adaptateurs USB, puis éteins (appui long sur le bouton). Si le portable ne se rallume plus, utilise le petit trou de secours (bouton Novo, sur le côté) avec un trombone : choisis « Normal Startup ».",
      "Réglages du BIOS conseillés pour un portable serveur (touche F2 ou bouton Novo → BIOS Setup) : démarrage USB désactivé, Fast Boot désactivé, démarrage réseau (PXE) désactivé, disque interne en premier. F10 pour enregistrer.",
      "Si la machine bloque parfois au démarrage : Système → Redémarrage après une mise à jour → « À la main ». Le serveur ne redémarrera plus seul la nuit ; Jabba te prévient et tu redémarres quand tu es devant.",
      "Si les blocages continuent malgré tout : mise à jour du BIOS (site du fabricant), ou changer de machine.",
    ],
  ],
  [
    "Jellyfin, Nextcloud ou un autre outil ne s'ouvre pas",
    [
      "Établi : l'interrupteur de l'outil doit être activé.",
      "Utilise « Redémarrer » sur l'outil, puis attends une minute.",
      "Un outil qui affiche « illisible » ou « arrêté » est aussi signalé dans le Journal (Système).",
    ],
  ],
];

pages.aide = async () => {
  content.innerHTML = `
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
