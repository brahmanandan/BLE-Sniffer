// sw.js — offline shell cache. The app shell is network-first (the bridge is on the LAN,
// so this is fast and an updated UI shows on the first reload), falling back to the
// cache when the bridge is unreachable. The WebSocket, /api and /files never touch it.

const CACHE = "remote-jig-v14";
const SHELL = [
  "./", "index.html", "css/app.css", "manifest.webmanifest",
  "js/app.js", "js/ws.js", "js/state.js", "js/ui.js", "js/remote_layout.js",
  "js/tab_connect.js", "js/gatt_info.js", "js/profile.js", "js/tab_keys.js", "js/tab_voice.js", "js/tab_ir.js", "js/tab_test.js",
  "icons/icon.svg", "icons/icon-192.png", "icons/icon-512.png", "icons/icon-maskable-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/ws") || url.pathname.startsWith("/api/") || url.pathname.startsWith("/files/")) return;
  e.respondWith(
    caches.open(CACHE).then((cache) =>
      fetch(e.request)
        .then((r) => { if (r.ok) cache.put(e.request, r.clone()); return r; })
        .catch(() => cache.match(e.request, { ignoreSearch: true }))
        .then((r) => r || new Response("offline", { status: 503 })),
    ),
  );
});
