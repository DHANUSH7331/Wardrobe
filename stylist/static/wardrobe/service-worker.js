const CACHE_NAME = 'atelier-shell-v1';
const OFFLINE_URL = '/static/wardrobe/offline.html';
const SHELL_URLS = [
    OFFLINE_URL,
    '/static/wardrobe/css/style.css',
    '/static/wardrobe/js/app.js',
    '/static/wardrobe/icons/favicon.svg',
    '/static/wardrobe/icons/icon-192.png',
    '/static/wardrobe/icons/icon-512.png',
    '/static/wardrobe/manifest.webmanifest',
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
                    .filter((cacheName) => cacheName.startsWith('atelier-shell-')
                        && cacheName !== CACHE_NAME)
                    .map((cacheName) => caches.delete(cacheName)),
            ))
            .then(() => self.clients.claim()),
    );
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET' || new URL(request.url).origin !== self.location.origin) {
        return;
    }

    if (request.mode === 'navigate') {
        event.respondWith(
            fetch(request).catch(() => caches.match(OFFLINE_URL)),
        );
        return;
    }

    if (SHELL_URLS.includes(new URL(request.url).pathname)) {
        event.respondWith(
            caches.match(request).then((cachedResponse) => cachedResponse || fetch(request)),
        );
    }
});
