const CACHE_NAME = 'polymath-shell-v1';
const APP_SHELL = ['./', './index.html'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys
          .filter(key => key !== CACHE_NAME)
          .map(key => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

function networkFirst(request) {
  return caches.open(CACHE_NAME).then(cache => {
    return fetch(request)
      .then(response => {
        if (response && response.ok) {
          cache.put(request, response.clone());
        }
        return response;
      })
      .catch(() => cache.match(request));
  });
}

function staleWhileRevalidate(request) {
  return caches.open(CACHE_NAME).then(cache => {
    return cache.match(request).then(cached => {
      const fetchPromise = fetch(request)
        .then(response => {
          if (response && response.ok) {
            cache.put(request, response.clone());
          }
          return response;
        })
        .catch(() => cached);

      return cached || fetchPromise;
    });
  });
}

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  if (event.request.method !== 'GET') return;

  if (url.origin === location.origin) {
    if (url.pathname.endsWith('/vital.json')) {
      event.respondWith(staleWhileRevalidate(event.request));
      return;
    }

    event.respondWith(networkFirst(event.request));
  }
});
