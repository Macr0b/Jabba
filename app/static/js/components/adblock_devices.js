// Bloqueur de pubs (AdGuard Home) appareil par appareil, dans la carte de l'Établi.
// Aucun appareil n'est filtré au départ : on allume ceux qu'on veut, un interrupteur par appareil.
//
// Sommaire :
//   partagé : renderAdblockDevices
//   interne : deviceRowHtml, toggleDevice
import { toast } from "./dialogs.js";
import { api } from "../core/api.js";
import { escapeHtml } from "../core/dom.js";
import { t } from "../core/i18n.js";

function deviceRowHtml(device) {
  const where = device.tailscale ? t("Tailscale") : t("Maison");
  return `<li class="ligne-appareil">
      <div><strong>${escapeHtml(device.nom)}</strong><div class="note">${escapeHtml(where)} · ${escapeHtml(device.ip)}</div></div>
      <button class="interrupteur" role="switch" aria-checked="${device.protege}" aria-label="${escapeHtml(t`Bloqueur de pubs pour ${device.nom}`)}" data-appareil="${escapeHtml(device.ip)}"><span></span></button>
    </li>`;
}

async function toggleDevice(button) {
  const protect = button.getAttribute("aria-checked") !== "true";
  button.disabled = true;
  try {
    await api("/api/adguard/appareils", { method: "POST", json: { ip: button.dataset.appareil, protege: protect } });
    button.setAttribute("aria-checked", String(protect));
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

export async function renderAdblockDevices(panel) {
  panel.innerHTML = `<p class="note">${escapeHtml(t("Recherche des appareils…"))}</p>`;
  let result;
  try {
    result = await api("/api/adguard/appareils");
  } catch (error) {
    panel.innerHTML = `<p class="note alerte-memoire">${escapeHtml(error.message)}</p>`;
    return;
  }
  if (!result.pret) {
    panel.innerHTML = `<p class="note alerte-memoire">${escapeHtml(result.message)}</p>`;
    return;
  }
  const intro = t(
    "Le bloqueur ne filtre que les appareils allumés ici. Un nouvel appareil passe sans filtre jusqu'à ce que tu l'allumes.",
  );
  const empty = t(
    "Aucun appareil vu pour l'instant : un appareil apparaît ici dès qu'il utilise le serveur comme DNS (réglage de la box, ou de l'appareil).",
  );
  panel.innerHTML = `<p class="note">${escapeHtml(intro)}</p>
    ${result.appareils.length ? `<ul class="liste-appareils">${result.appareils.map(deviceRowHtml).join("")}</ul>` : `<p class="note">${escapeHtml(empty)}</p>`}`;
  panel.querySelectorAll("[data-appareil]").forEach((button) => {
    button.onclick = () => toggleDevice(button);
  });
}
