// Mise en forme des nombres, tailles, durées et dates, à la française.
//
// Sommaire :
//   partagé : formatBytes, formatPercent, formatDuration, formatWhen, timeAgo, formatGigabytes, timeOf, dayOf, shortDuration

import { t } from "./i18n.js";
export function formatBytes(item) {
  if (item == null) return "—";
  const user = ["o", "Ko", "Mo", "Go", "To"];
  let index = 0;
  while (item >= 1000 && index < user.length - 1) {
    item /= 1024;
    index++;
  }
  return (
    (item >= 100 || index === 0 ? Math.round(item) : item.toFixed(1)).toString().replace(".", ",") + " " + user[index]
  );
}
export function formatPercent(user) {
  return user && user.total ? Math.round((100 * user.utilise) / user.total) : 0;
}
export function formatDuration(value) {
  const session = Math.floor(value / 86400),
    hours = Math.floor((value % 86400) / 3600);
  if (session) return `${session} j ${hours} h`;
  return `${hours} h ${Math.floor((value % 3600) / 60)} min`;
}
export function formatWhen(isoDate) {
  if (!isoDate) return "—";
  const data = new Date(isoDate),
    now = new Date();
  const days = Math.floor((new Date(now.toDateString()) - new Date(data.toDateString())) / 86400000);
  if (days === 0) return "Aujourd'hui " + data.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (days === 1) return t("Hier");
  if (days < 7)
    return data.toLocaleDateString("fr-FR", { weekday: "long" }).replace(/^./, (item) => item.toUpperCase());
  return data.toLocaleDateString("fr-FR", {
    day: "numeric",
    month: "short",
    year: data.getFullYear() === now.getFullYear() ? undefined : "numeric",
  });
}
export const timeAgo = (value) => (value ? formatWhen(new Date(value * 1000).toISOString()) : t("Jamais"));

// ------------------------------------------------------------ comptes Nextcloud
export const formatGigabytes = (item) =>
  item == null ? t("illimité") : String(Math.round((item / 1073741824) * 10) / 10).replace(".", ",") + " Go";

// ------------------------------------------------------------ sauvegardes
export const timeOf = (value) =>
  new Date(value * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
export const dayOf = (value) =>
  new Date(value * 1000).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
export const shortDuration = (value) =>
  value < 60
    ? `${Math.max(1, Math.round(value))} s`
    : value < 3600
      ? `${Math.round(value / 60)} min`
      : `${Math.floor(value / 3600)} h ${String(Math.round((value % 3600) / 60)).padStart(2, "0")}`;
