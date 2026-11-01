// Journl service worker: speeds up the PWA cold start by serving the static
// app shell from cache. Only /static/ GETs are touched — journal content, API
// responses and the HTML shell always come from the network, so a stale cache
// can never show stale or foreign data.
//
// The cache name carries the app version from the registration URL (?v=…).
// A new release therefore installs a new worker, and activate() deletes every
// older journl-static-* cache. A fixed name would keep one entry per asset
// version forever; that unbounded growth is exactly what makes an iOS PWA
// start slow.
const SW_VERSION = new URL(self.location).searchParams.get('v') || 'v1';
const CACHE_NAME = `journl-static-${SW_VERSION}`;

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((key) => key.startsWith('journl-static-') && key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (!url.pathname.startsWith('/static/')) return;
  // Only the ?v= cache-buster proves the URL never changes, so only those may
  // be served cache-first. An asset without it is fetched first and only falls
  // back to cache offline — cache-first would freeze it forever.
  const immutable = Boolean(url.searchParams.get('v'));
  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cached = await cache.match(event.request);
      if (cached && immutable) return cached;
      try {
        const response = await fetch(event.request);
        if (response && response.ok && response.type === 'basic') {
          cache.put(event.request, response.clone());
        }
        return response;
      } catch (error) {
        if (cached) return cached;
        throw error;
      }
    })
  );
});
