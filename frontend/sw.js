/* Kuma FlowMap service worker — makes the app installable + fast, without ever staling live data.
   Strategy:
     - App shell (page + vendored JS/CSS + icons): cache-first, refreshed in the background.
     - Everything under /api and /ws: never touched (always goes straight to the network so the
       monitoring data and the live WebSocket stay real-time). */
const CACHE = "kuma-flowmap-v1";
const SHELL = [
  "/",
  "/static/vendor/drawflow.min.js",
  "/static/vendor/drawflow.min.css",
  "/static/vendor/icons.js",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/apple-touch-icon.png"
];

self.addEventListener("install", function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // live data + socket: always network, never cached (keeps monitoring real-time)
  if (url.pathname.startsWith("/api") || url.pathname.startsWith("/ws")) return;

  // NETWORK-FIRST for the app shell: always serve the freshest file when online (so updates you
  // deploy show up immediately), keep a copy in the cache, and fall back to that copy only when
  // the network is unavailable — so the app still opens offline.
  e.respondWith(
    fetch(req).then(function (res) {
      if (res && res.status === 200) { var copy = res.clone(); caches.open(CACHE).then(function (c) { c.put(req, copy); }); }
      return res;
    }).catch(function () {
      return caches.match(req).then(function (cached) {
        return cached || (req.mode === "navigate" ? caches.match("/") : Response.error());
      });
    })
  );
});
