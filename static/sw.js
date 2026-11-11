// Service worker de Comparam: permite instalar la app y muestra un aviso si no hay internet.
// Las búsquedas siempre van a la red (los precios cambian); solo se guardan los íconos y la
// página de «sin conexión».
// «Mis gastos» (/gastos) sí funciona sin internet: se usa la última versión guardada.
const CACHE = "comparam-v2";
const BASICOS = ["/static/offline.html", "/static/icon-192.png", "/static/manifest.webmanifest",
                 "/gastos", "/static/gastos.webmanifest", "/static/gastos-192.png"];

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
  const ruta = new URL(e.request.url).pathname;
  if (e.request.mode === "navigate" && ruta === "/gastos") {
    // Red primero (para tener la versión nueva) y, sin conexión, la copia guardada.
    e.respondWith(
      fetch(e.request)
        .then((r) => { const copia = r.clone(); caches.open(CACHE).then((c) => c.put("/gastos", copia)); return r; })
        .catch(() => caches.match("/gastos"))
    );
  } else if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() => caches.match("/static/offline.html")));
  } else if (e.request.url.includes("/static/")) {
    e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
  }
});
