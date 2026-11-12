// Service worker de Comparam: permite instalar la app y muestra un aviso si no hay internet.
// Las búsquedas siempre van a la red (los precios cambian); solo se guardan los íconos y la
// página de «sin conexión».
const CACHE = "comparam-v3";
const BASICOS = ["/static/offline.html", "/static/icon-192.png", "/static/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(BASICOS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() => caches.match("/static/offline.html")));
  } else if (e.request.url.includes("/static/")) {
    e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
  }
});
