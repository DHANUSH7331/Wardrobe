const CACHE_NAME = 'atelier-pages-shell-v2';
const BASE_URL = new URL('./', self.location.href);
const OFFLINE_URL = new URL('offline.html', BASE_URL).href;
const SHELL_URLS = [
    BASE_URL.href,
    new URL('index.html', BASE_URL).href,
    OFFLINE_URL,
    new URL('manifest.webmanifest', BASE_URL).href,
    new URL('stylist/static/wardrobe/icons/icon-192.png', BASE_URL).href,
    new URL('stylist/static/wardrobe/icons/icon-512.png', BASE_URL).href,
    new URL('stylist/static/wardrobe/icons/favicon.svg', BASE_URL).href,
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(SHELL_URLS))
            .then(() => self.skipWaiting()),
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((cacheNames) => Promise.all(
                cacheNames
                    .filter((cacheName) => cacheName.startsWith('atelier-pages-shell-')
                        && cacheName !== CACHE_NAME)
                    .map((cacheName) => caches.delete(cacheName)),
            ))
            .then(() => self.clients.claim()),
    );
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    const requestUrl = new URL(request.url);

    if (request.method !== 'GET' || requestUrl.origin !== self.location.origin) {
        return;
    }

    if (request.mode === 'navigate') {
        event.respondWith(
            fetch(request, { cache: 'no-cache' }).catch(async () => (
                await caches.match(request) || caches.match(OFFLINE_URL)
            )),
        );
        return;
    }

    if (SHELL_URLS.includes(requestUrl.href)) {
        event.respondWith(
            fetch(request, { cache: 'no-cache' }).then(async (response) => {
                if (response.ok) {
                    const cache = await caches.open(CACHE_NAME);
                    await cache.put(request, response.clone());
                }
                return response;
            }).catch(async () => caches.match(request)),
        );
    }
});
