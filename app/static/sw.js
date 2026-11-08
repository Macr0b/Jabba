// Service worker : rend l'appli installable, affiche les alertes du serveur, et
// garde une copie de l'interface (jamais des données) pour pouvoir dire
// « serveur injoignable » et ouvrir l'Aide quand le serveur ne répond plus.
// Le réseau passe toujours en premier : la copie ne sert qu'en cas d'échec.
const COQUILLE = "jabba-coquille";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;
  const page = e.request.mode === "navigate";
  if (!page && !url.pathname.startsWith("/static/")) return;   // les données (/api) ne passent jamais par la copie
  e.respondWith((async () => {
    const cache = await caches.open(COQUILLE);
    try {
      const r = await fetch(e.request);
      if (r.ok) cache.put(page ? "/" : e.request, r.clone());
      if (page && r.status >= 502 && r.status <= 504) return (await cache.match("/")) || r;   // appli arrêtée
      return r;
    } catch (err) {
      const copie = (await cache.match(page ? "/" : e.request)) || (!page && (await cache.match(e.request, { ignoreSearch: true })));
      if (copie) return copie;
      throw err;
    }
  })());
});

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
