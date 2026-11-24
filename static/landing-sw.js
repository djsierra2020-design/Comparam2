// Service worker de «Landings IA» cuando se publica como sitio propio.
// Permite instalarla como app y abrirla sin internet (la IA sí necesita conexión).
const CACHE = "landings-ia-v1";
const BASICOS = ["/", "/static/landing.webmanifest", "/static/landing-192.png", "/static/landing-512.png"];

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
  if (e.request.method !== "GET" || new URL(e.request.url).origin !== location.origin) return;
  if (e.request.mode === "navigate") {
    // Red primero para tener siempre la última versión; sin conexión, la copia guardada.
    e.respondWith(
      fetch(e.request)
        .then((r) => { const copia = r.clone(); caches.open(CACHE).then((c) => c.put("/", copia)); return r; })
        .catch(() => caches.match("/"))
    );
  } else {
    e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
  }
});
