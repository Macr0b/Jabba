// « Connecter un appareil », dans le menu de Nextcloud : comme Dropbox, un dossier sur l'ordinateur qui se
// synchronise avec le serveur, dans les deux sens (ajouts, changements et suppressions). Jabba reconnaît
// l'appareil, donne le bon téléchargement et l'adresse à coller ; la connexion se valide dans le navigateur.
//
// Sommaire :
//   partagé : renderConnectDevice
//   interne : deviceKind, serverAddress, STEPS, DOWNLOADS, stepsHtml
import { toast } from "./dialogs.js";
import { api } from "../core/api.js";
import { escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";

const DOWNLOADS = {
  mac: ["https://nextcloud.com/install/#install-clients", t("Télécharger Nextcloud pour Mac")],
  windows: ["https://nextcloud.com/install/#install-clients", t("Télécharger Nextcloud pour Windows")],
  linux: ["https://nextcloud.com/install/#install-clients", t("Télécharger Nextcloud pour Linux")],
  iphone: ["https://apps.apple.com/app/id1125420102", t("Nextcloud sur l'App Store")],
  android: ["https://play.google.com/store/apps/details?id=com.nextcloud.client", t("Nextcloud sur Google Play")],
};

const COMPUTER_END = [
  t("Le navigateur s'ouvre : connecte-toi à Nextcloud (ou « Autoriser l'accès » si tu l'es déjà)."),
  t(
    "L'appli demande où créer le dossier : laisse « Nextcloud » dans ton dossier personnel, ou choisis-en un autre. Garde « Tout synchroniser ».",
  ),
  t(
    "C'est fait : ce que tu mets dans ce dossier part sur le serveur, ce que tu y supprimes est supprimé partout (et reste 30 jours dans la corbeille de Nextcloud).",
  ),
];

const STEPS = {
  mac: [
    t("Télécharge la version « standard » pour macOS (pas « Virtual Files ») et installe-la."),
    t("Ouvre Nextcloud, puis « Se connecter » et colle l'adresse ci-dessous."),
    ...COMPUTER_END,
  ],
  windows: [
    t("Télécharge Nextcloud pour Windows et installe-le."),
    t("Ouvre Nextcloud, puis « Se connecter » et colle l'adresse ci-dessous."),
    ...COMPUTER_END,
  ],
  linux: [
    t("Installe Nextcloud (paquet « nextcloud-desktop », ou le fichier AppImage du site)."),
    t("Ouvre Nextcloud, puis « Se connecter » et colle l'adresse ci-dessous."),
    ...COMPUTER_END,
  ],
  iphone: [
    t("Installe l'appli Nextcloud depuis l'App Store."),
    t("Ouvre-la, « Se connecter », colle l'adresse ci-dessous, puis connecte-toi."),
    t(
      "Dans l'app Fichiers : Parcourir → ⋯ → Modifier → active Nextcloud. Tes dossiers y apparaissent, téléchargés à la demande.",
    ),
  ],
  android: [
    t("Installe l'appli Nextcloud depuis Google Play."),
    t("Ouvre-la, « Se connecter », colle l'adresse ci-dessous, puis connecte-toi."),
  ],
};

function deviceKind() {
  const agent = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(agent) || (navigator.maxTouchPoints > 1 && /Macintosh/.test(agent))) return "iphone";
  if (/Android/.test(agent)) return "android";
  if (/Windows/.test(agent)) return "windows";
  if (/Macintosh/.test(agent)) return "mac";
  return "linux";
}

/** L'adresse publique (domaine) si elle marche, sinon celle de Tailscale. */
async function serverAddress(item) {
  const domain = await api("/api/domaine").catch(() => null);
  if (domain && domain.active && domain.dns_ok && domain.certificate_ok)
    return { url: `https://${domain.address}`, everywhere: true };
  return { url: item.link, everywhere: false };
}

function stepsHtml(kind) {
  const [link, label] = DOWNLOADS[kind];
  return `<ol class="note">${STEPS[kind].map((step) => `<li>${escapeHtml(step)}</li>`).join("")}</ol>
    <p><a class="btn" href="${escapeHtml(link)}" target="_blank" rel="noopener">${escapeHtml(label)}</a></p>`;
}

export async function renderConnectDevice(panel, item) {
  panel.innerHTML = `<p class="note">${escapeHtml(t("Un instant…"))}</p>`;
  const kind = deviceKind();
  const { url, everywhere } = await serverAddress(item);
  const names = {
    mac: t("Mac"),
    windows: t("Windows"),
    linux: t("Linux"),
    iphone: "iPhone / iPad",
    android: t("Android"),
  };
  const others = Object.keys(STEPS).filter((other) => other !== kind);
  panel.innerHTML = t`<p class="note">Comme Dropbox : un dossier sur l'ordinateur, synchronisé avec le serveur dans les deux sens. Sur le téléphone, tes fichiers à la demande.</p>
    <h3>Sur cet appareil (${escapeHtml(names[kind])})</h3>
    ${stepsHtml(kind)}
    <label class="champ"><span>Adresse du serveur</span><input type="text" readonly value="${escapeHtml(url)}" data-adresse></label>
    <div class="actions"><button class="btn btn-p" type="button" data-copier>Copier l'adresse</button></div>
    <p class="note">${
      everywhere
        ? t("Cette adresse marche partout, sans Tailscale : la même pour tes proches.")
        : t(
            "Cette adresse ne marche qu'avec Tailscale allumé. Pour une adresse qui marche partout : Réglages → Internet → Accès par le domaine.",
          )
    }</p>
    ${others
      .map((other) => `<details><summary>${escapeHtml(names[other])}</summary>${stepsHtml(other)}</details>`)
      .join("")}`;
  panel.querySelector("[data-copier]").onclick = async () => {
    try {
      await navigator.clipboard.writeText(url);
      toast(t("Adresse copiée"));
    } catch {
      panel.querySelector("[data-adresse]").select();
    }
  };
}
