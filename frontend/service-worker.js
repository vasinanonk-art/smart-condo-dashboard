'use strict';

const BUILD_VERSION = '__ASSET_VERSION__';
const CACHE_PREFIX = 'smart-condo-shell-';
const CACHE_NAME = `${CACHE_PREFIX}${BUILD_VERSION}`;
const CACHEABLE_ASSET = /\.(?:css|js|png|svg|webmanifest)$/i;

function isNavigation(request) {
  return request.mode === 'navigate';
}

function isSensitivePath(pathname) {
  return pathname.startsWith('/api/')
    || pathname === '/login'
    || pathname === '/logout'
    || pathname.startsWith('/camera/')
    || pathname.startsWith('/auth/');
}

function isVersionedAsset(request) {
  if (request.method !== 'GET') return false;
  const url = new URL(request.url);
  return url.origin === self.location.origin
    && url.pathname.startsWith('/assets/')
    && url.searchParams.get('v') === BUILD_VERSION
    && [...url.searchParams.keys()].length === 1
    && CACHEABLE_ASSET.test(url.pathname);
}

function connectionFailure() {
  return new Response(`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#09090B"><title>Smart Condo unavailable</title><style>html{color-scheme:dark}body{display:grid;min-height:100vh;margin:0;padding:24px;place-items:center;background:#09090b;color:#f5f7fb;font:16px/1.5 Inter,system-ui,sans-serif;text-align:center}.card{max-width:420px;padding:32px;border:1px solid rgba(255,255,255,.1);border-radius:24px;background:#111827}h1{margin:0 0 12px;font-size:24px}p{margin:0;color:#aeb8c7}</style></head><body><main class="card"><h1>Connection unavailable</h1><p>Smart Condo could not reach the dashboard. Check the local network or secure connection, then try again.</p></main></body></html>`, {
    status:503,
    headers:{'Content-Type':'text/html; charset=utf-8', 'Cache-Control':'no-store'},
  });
}

async function networkNavigation(request) {
  try {
    return await fetch(request);
  } catch (_error) {
    return connectionFailure();
  }
}

async function immutableAsset(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok && response.type !== 'opaque' && !response.redirected) {
    await cache.put(request, response.clone());
  }
  return response;
}

self.addEventListener('install', event => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names
      .filter(name => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
      .map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const {request} = event;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (isNavigation(request)) {
    event.respondWith(networkNavigation(request));
    return;
  }
  if (isSensitivePath(url.pathname)) {
    event.respondWith(fetch(request));
    return;
  }
  if (isVersionedAsset(request)) {
    event.respondWith(immutableAsset(request));
  }
});
