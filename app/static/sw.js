// Journl service worker: speeds up the PWA cold start by serving the static
// app shell from cache. Only /static/ GETs are cached — journal content, API
// responses and the HTML shell always come from the network, so a stale cache
// can never show stale or foreign data.
const CACHE_NAME = 'journl-static-v1';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (!url.pathname.startsWith('/static/')) return;
  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cached = await cache.match(event.request);
      if (cached) return cached;
      const response = await fetch(event.request);
      if (response && response.ok && response.type === 'basic') {
        cache.put(event.request, response.clone());
      }
      return response;
    })
  );
});
