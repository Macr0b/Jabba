// Alertes sur cet appareil (notifications) : activer, choisir les types, essayer.
//
// Sommaire :
//   partagé : alertsCard
//   interne : keyToBytes, pushSupported, isIOS, currentSubscription, enableAlerts
import { toast } from "./dialogs.js";
import { api } from "../core/api.js";
import { $, escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";

// ------------------------------------------------------------ alertes (notifications)
function keyToBytes(base64) {
  const value = atob(base64.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((base64.length + 3) % 4));
  return Uint8Array.from(value, (item) => item.charCodeAt(0));
}
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const isIOS = () =>
  /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

async function currentSubscription() {
  if (!pushSupported()) return null;
  const registration = await navigator.serviceWorker.ready;
  return registration.pushManager.getSubscription();
}

async function enableAlerts(key) {
  if ((await Notification.requestPermission()) !== "granted")
    throw new Error(t("Alertes refusées. Autorise-les dans les réglages de l'appareil (Notifications → Jabba)."));
  const registration = await navigator.serviceWorker.ready;
  let subscription = await registration.pushManager.getSubscription();
  if (subscription) await subscription.unsubscribe();
  subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: keyToBytes(key),
  });
  const device = isIOS()
    ? t("iPhone ou iPad")
    : /Mac/.test(navigator.userAgent)
      ? t("Mac")
      : navigator.platform || t("Appareil");
  await api("/api/alertes/abonnement", {
    method: "POST",
    json: { subscription: subscription.toJSON(), device },
  });
}

export async function alertsCard() {
  const element = $("#alertes");
  if (!element) return;
  const data = await api("/api/alertes");
  let here = false;
  let reason = "";
  if (!pushSupported())
    reason = isIOS()
      ? t(
          "Sur iPhone, ajoute d'abord l'appli à l'écran d'accueil (bouton Partager → « Sur l'écran d'accueil »), puis ouvre-la depuis son icône et reviens ici.",
        )
      : t("Ce navigateur ne sait pas recevoir d'alertes. Utilise Safari (Mac, iPhone) ou Chrome.");
  else {
    const subscription = await currentSubscription();
    if (subscription)
      here = (await api("/api/alertes/abonnement/etat", { method: "POST", json: { endpoint: subscription.endpoint } }))
        .subscribed;
  }
  element.innerHTML = t`<h2>Alertes</h2>
    <p class="note" style="margin:8px 0 12px">${data.devices ? t`${data.devices} appareil${data.devices > 1 ? t("s reçoivent") : t(" reçoit")} les alertes.` : t("Aucun appareil ne reçoit les alertes.")} ${here ? t("<strong>Celui-ci en fait partie.</strong>") : ""}</p>
    ${reason ? `<div class="bandeau" style="margin-bottom:12px"><span>${escapeHtml(reason)}</span></div>` : ""}
    <div class="actions" style="margin-bottom:14px">
      ${reason ? "" : here ? t`<button class="btn" id="alertes-off">Ne plus les recevoir ici</button>` : t`<button class="btn btn-p" id="alertes-on">Recevoir les alertes sur cet appareil</button>`}
      ${data.devices ? t`<button class="btn" id="alertes-essai">Envoyer une alerte d'essai</button>` : ""}
    </div>
    <div class="types-alertes">${data.types
      .map(
        (
          item,
        ) => `<label class="ligne-reglage"><span><strong>${escapeHtml(item.name)}</strong><br><span class="note">${escapeHtml(item.detail)}</span></span>
      <button class="interrupteur" role="switch" aria-checked="${item.active}" data-type="${escapeHtml(item.id)}" aria-label="${escapeHtml(item.name)}"><span></span></button></label>`,
      )
      .join("")}</div>
    <p class="note">Ces réglages valent pour tous tes appareils. Tout est aussi noté dans le journal, même ce qui ne t'est pas envoyé.</p>`;
  const on = $("#alertes-on");
  const off = $("#alertes-off");
  const attempt = $("#alertes-essai");
  if (on)
    on.onclick = async () => {
      on.disabled = true;
      try {
        await enableAlerts(data.key);
        toast(t("Alertes activées sur cet appareil"));
      } catch (error) {
        toast(error.message, true);
      }
      alertsCard().catch(() => {});
    };
  if (off)
    off.onclick = async () => {
      try {
        const subscription = await currentSubscription();
        if (subscription) {
          await api("/api/alertes/abonnement/retirer", { method: "POST", json: { endpoint: subscription.endpoint } });
          await subscription.unsubscribe();
        }
        toast(t("Cet appareil ne recevra plus d'alertes"));
      } catch (error) {
        toast(error.message, true);
      }
      alertsCard().catch(() => {});
    };
  if (attempt)
    attempt.onclick = async () => {
      try {
        await api("/api/alertes/essai", { method: "POST" });
        toast(t("Alerte d'essai envoyée : elle arrive dans quelques secondes"));
      } catch (error) {
        toast(error.message, true);
      }
    };
  element.querySelectorAll("[data-type]").forEach((element) =>
    element.addEventListener("click", async () => {
      const active = element.getAttribute("aria-checked") !== "true";
      element.setAttribute("aria-checked", String(active));
      try {
        await api("/api/alertes/types", { method: "POST", json: { type: element.dataset.type, active } });
      } catch (error) {
        toast(error.message, true);
        element.setAttribute("aria-checked", String(!active));
      }
    }),
  );
}
