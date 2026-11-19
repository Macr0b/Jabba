// Aide et informations d'un outil de l'Établi, affichées dans sa carte (menu « ••• »).
//
// Sommaire :
//   partagé : helpHtml, infoHtml, canUninstall, uninstallUrl
import { escapeHtml } from "../core/dom.js";
import { formatBytes } from "../core/format.js";
import { t } from "../core/i18n.js";

const HELP = {
  jellyfin: [
    t(
      "Tes films et séries, en streaming sur la télé, le téléphone ou l'ordinateur. Les disques ajoutés à Jabba apparaissent dans le dossier /disques.",
    ),
    "https://jellyfin.org/docs/",
  ],
  nextcloud: [
    t(
      "Tes fichiers, ton agenda et tes contacts, synchronisés sur le Mac et l'iPhone (applis Nextcloud). Les comptes se gèrent dans Comptes.",
    ),
    "https://docs.nextcloud.com/",
  ],
  dockge: [
    t(
      "Pour les réglages avancés des outils (fichiers compose). Il a la main sur tout le serveur : n'y touche que si tu sais ce que tu fais.",
    ),
    "https://github.com/louislam/dockge",
  ],
  antivirus: [
    t(
      "ClamAV analyse les dépôts des invités, les nouveaux fichiers et les disques cochés. Un fichier infecté est mis à l'écart, jamais supprimé.",
    ),
    "https://docs.clamav.net/",
  ],
  vaultwarden: [
    t(
      "Tes mots de passe, dans les applis Bitwarden (Mac, iPhone, navigateur) : à l'ouverture, choisis « auto-hébergé » et donne l'adresse du bouton Ouvrir. Joignable seulement par Tailscale.",
    ),
    "https://github.com/dani-garcia/vaultwarden/wiki",
  ],
  immich: [
    t(
      "Tes photos et vidéos : installe l'appli Immich sur l'iPhone, donne l'adresse du bouton Ouvrir et active la sauvegarde automatique. Les photos vont dans Photos-Immich sur le disque de données.",
    ),
    "https://docs.immich.app/",
  ],
  "time-machine": [
    t(
      "Les sauvegardes Time Machine des Mac, par le réseau : sur le Mac, Réglages Système → Général → Time Machine → Ajouter un disque de sauvegarde → TimeMachine.",
    ),
    "",
  ],
  "adguard-home": [
    t(
      "Bloque publicités et traqueurs, seulement pour les appareils que tu choisis : menu … → Appareils, un interrupteur par appareil. Un nouvel appareil passe sans filtre. Pour qu'un appareil apparaisse, il doit utiliser le serveur comme DNS : dans la box (Freebox OS : Paramètres → DHCP) pour toute la maison, ou dans les réglages Wi-Fi de l'appareil. La page d'AdGuard (compte « admin ») montre le détail de ce qui est bloqué.",
    ),
    "https://github.com/AdguardTeam/AdGuardHome/wiki",
  ],
  paperless: [
    t(
      "Dépose tes papiers (PDF, photos de documents) dans le dossier Paperless/A-classer de Fichiers : Paperless lit le texte, les range et les retrouve par recherche. Connexion : compte « admin » et le mot de passe choisi à l'activation.",
    ),
    "https://docs.paperless-ngx.com/",
  ],
  homebox: [
    t(
      "L'inventaire du fablab ou de la maison : lieux, objets, quantités, étiquettes à QR code. Le premier compte créé devient le propriétaire ; invite les autres depuis Homebox.",
    ),
    "https://homebox.software/",
  ],
  ntfy: [
    t(
      "Installe l'appli ntfy sur le téléphone, ajoute ce serveur (adresse de la carte), puis abonne-toi au sujet indiqué dans Système → Alertes : les alertes de Jabba et les rappels d'entretien y arrivent. Les types d'alertes se règlent au même endroit.",
    ),
    "https://docs.ntfy.sh/subscribe/phone/",
  ],
  changedetection: [
    t(
      "Colle l'adresse d'une page (un prix, un produit en rupture, une notice) : il la relit régulièrement et te montre ce qui a changé. Pour être prévenu sur le téléphone avec ntfy, mets comme notification : ntfy://127.0.0.1:2586/ton-sujet.",
    ),
    "https://changedetection.io/tutorials",
  ],
  atelier: [
    t(
      "Le carnet de chaque machine : journal des interventions et des pannes, réglages, pièces changées, rappels d'entretien avec alerte à l'échéance, et le dossier de ses manuels.",
    ),
    "",
  ],
};

/** Les outils qu'on peut désinstaller depuis l'Établi (installés par Jabba), et comment. */
const UNINSTALL = {
  antivirus: "/api/antivirus/retirer",
  vaultwarden: "/api/etabli/vaultwarden/retirer",
  immich: "/api/etabli/immich/retirer",
  "time-machine": "/api/etabli/time-machine/retirer",
  "adguard-home": "/api/etabli/adguard-home/retirer",
  paperless: "/api/etabli/paperless/retirer",
  homebox: "/api/etabli/homebox/retirer",
  ntfy: "/api/etabli/ntfy/retirer",
  changedetection: "/api/etabli/changedetection/retirer",
  atelier: "/api/etabli/atelier/retirer",
};

export const canUninstall = (id) => id in UNINSTALL;
export const uninstallUrl = (id) => UNINSTALL[id];

export function helpHtml(id) {
  const [text, link] = HELP[id] || [t("Pas d'aide écrite pour cet outil."), ""];
  return `<p>${escapeHtml(text)}</p>${link ? t`<p><a href="${escapeHtml(link)}" target="_blank" rel="noopener">Documentation de l'outil ↗</a></p>` : ""}`;
}

/** Ce qu'on sait de l'outil : état, version, mémoire, dossier, conteneurs. */
export function infoHtml(item, version, usage) {
  const lines = [
    [t("État"), item.etat || t("pas installé")],
    [t("Version"), version || "—"],
    [t("Mémoire"), usage ? formatBytes(usage.memoire) : item.memoire ? t`environ ${formatBytes(item.memoire)}` : "—"],
    [t("Dossier"), item.dossier || "—"],
    [
      t("Conteneurs"),
      (item.conteneurs || []).map((container) => `${container.nom} (${container.etat})`).join(", ") || "—",
    ],
  ];
  return `<dl class="infos-outil">${lines.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join("")}</dl>`;
}
