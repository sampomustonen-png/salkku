/* Salkku – service worker: vastaanottaa push-ilmoitukset.
   Ilmoituksen sisältö on salattu päästä päähän; selain purkaa sen ennen tätä vaihetta. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = {body: e.data && e.data.text()}; }
  e.waitUntil(self.registration.showNotification(d.title || "Salkku", {
    body: d.body || "",
    tag: d.tag || undefined,
    icon: "icon-192.png",
    badge: "icon-192.png",
    data: {url: d.url || "./"},
  }));
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = new URL(e.notification.data?.url || "./", self.registration.scope).href;
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({type: "window", includeUncontrolled: true});
    for (const w of wins) { if (w.url.startsWith(self.registration.scope)) { await w.focus(); return; } }
    await self.clients.openWindow(url);
  })());
});
