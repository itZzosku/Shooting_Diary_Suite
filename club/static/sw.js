/* Club ammo service worker (scope /ammo/): the box form opens at the range without signal.
   - Box pages and box info (GET /ammo/api/b/...): network first, last good copy when offline.
   - Styles and fonts: cache first. Entries are queued by the page itself, never here. */
const VERSION = "a4";
const PAGE = "ammo-page", ASSETS = "ammo-assets-" + VERSION;

self.addEventListener("install", e => e.waitUntil(self.skipWaiting()));
self.addEventListener("activate", e => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k.startsWith("ammo-assets-") && k !== ASSETS) await caches.delete(k);
  await self.clients.claim();
})()));

async function networkFirst(req, key, mark) {
  try {
    const res = await fetch(req);
    if (res.ok && !res.redirected) (await caches.open(PAGE)).put(key || req, res.clone());
    return res;
  } catch (err) {
    const hit = await caches.match(key || req, {cacheName: PAGE});
    if (!hit) throw err;
    if (!mark) return hit;
    const h = new Headers(hit.headers); h.set("X-Offline", "1");     // the page says it's offline
    return new Response(await hit.blob(), {status: hit.status, headers: h});
  }
}
async function cacheFirst(req) {
  const hit = await caches.match(req, {cacheName: ASSETS});
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) (await caches.open(ASSETS)).put(req, res.clone());
  return res;
}

self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  const p = url.pathname;
  if (req.mode === "navigate" && /^\/ammo\/[a-z0-9-]+\/[A-Za-z0-9_-]{8,40}$/.test(p)) {
    e.respondWith(networkFirst(req, p).catch(() => new Response(
      "<p style='font:16px system-ui;padding:24px'>Offline. Open this box's page once while online and it works offline after that.</p>",
      {headers: {"Content-Type": "text/html; charset=utf-8"}})));
  } else if (p.startsWith("/ammo/api/b/")) {
    e.respondWith(networkFirst(req, p, true).catch(() =>
      new Response(JSON.stringify({error: "offline"}), {status: 503, headers: {"Content-Type": "application/json"}})));
  } else if (p.startsWith("/ammo/static/") || p.startsWith("/diary/static/fonts/") || p.startsWith("/diary/static/icons/")) {
    e.respondWith(cacheFirst(req));
  }
});
