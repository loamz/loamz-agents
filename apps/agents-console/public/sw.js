/* Only a public offline page is cached. Authenticated responses and chat history never enter Cache Storage. */
const SHELL = "loamz-messenger-shell-v1";
self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.add("/chat/offline.html")));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then(async (keys) => {
    await Promise.all(keys.filter((key) => key.startsWith("loamz-messenger-shell-") && key !== SHELL).map((key) => caches.delete(key)));
    await self.clients.claim();
  }));
});
self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate" || new URL(event.request.url).origin !== self.location.origin) return;
  event.respondWith(fetch(event.request).catch(() => caches.match("/chat/offline.html")));
});
self.addEventListener("push", (event) => {
  // E1 will fetch and decrypt in the local crypto owner. Until then, no content is put into a notification.
  event.waitUntil(self.registration.showNotification("Loamz", {
    body: "New message", icon: "/chat/favicon.svg", tag: "loamz-message", data: { url: "/chat/" },
  }));
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(async (clients) => {
    const client = clients.find((item) => new URL(item.url).pathname.startsWith("/chat"));
    if (client) await client.focus();
    else await self.clients.openWindow("/chat/");
  }));
});
