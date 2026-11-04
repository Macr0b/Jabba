// Service worker : rend l'appli installable et affiche les alertes du serveur.
// Rien n'est gardé en cache : l'appli affiche toujours l'état réel du serveur.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { titre: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.titre || "Jabba", {
    body: d.texte || "",
    icon: "/static/icone-192.png",
    badge: "/static/icone-192.png",
    data: { url: d.url || "/" },
    tag: d.titre || undefined,
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || "/", self.location.origin).href;
  e.waitUntil((async () => {
    const fenetres = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const f of fenetres) {
      if (f.url.startsWith(self.location.origin)) { await f.focus(); return f.navigate(url).catch(() => {}); }
    }
    return self.clients.openWindow(url);
  })());
});
