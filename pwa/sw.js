'use strict';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

// Optional payload fields honored by this SW (all strings, optional):
//   icon  -- per-message icon URL. Must start with "icons/", "https://", or "data:image/".
//   badge -- per-message monochrome badge URL. Same prefix rules as icon.
//   image -- large hero image URL (Android). Same prefix rules.
//   tag   -- collapses with prior notification sharing the same tag.
function safeAsset(v) {
  return typeof v === 'string'
    && (v.startsWith('icons/') || v.startsWith('https://') || v.startsWith('data:image/'))
    ? v : null;
}

self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; }
  catch { data = { title: 'Notification', body: event.data ? event.data.text() : '' }; }
  data.receivedMs = Date.now();
  const title = data.title || 'Notification';
  const baseBody = data.body || '';
  const src = (data.source && typeof data.source === 'object') ? data.source : {};
  const attribution = Object.values(src).filter(v => typeof v === 'string' && v).join(' \u00b7 ');
  const body = attribution ? (baseBody ? baseBody + '\n\u2014 ' + attribution : '\u2014 ' + attribution) : baseBody;
  const opts = {
    body,
    icon: safeAsset(data.icon) || 'icons/icon-192.png',
    badge: safeAsset(data.badge) || 'icons/badge-96.png',
    data,
  };
  const image = safeAsset(data.image);
  if (image) opts.image = image;
  if (typeof data.tag === 'string' && data.tag) opts.tag = data.tag;
  event.waitUntil(self.registration.showNotification(title, opts));
});

function urlSafeB64Encode(str) {
  // Encode UTF-8 string -> base64url (no padding).
  const bytes = new TextEncoder().encode(str);
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function idbPut(key, value) {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('push-relay', 1);
    req.onupgradeneeded = () => req.result.createObjectStore('kv');
    req.onerror = () => reject(req.error);
    req.onsuccess = () => {
      const db = req.result;
      const tx = db.transaction('kv', 'readwrite');
      tx.objectStore('kv').put(value, key);
      tx.oncomplete = () => { db.close(); resolve(); };
      tx.onerror = () => reject(tx.error);
    };
  });
}

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const data = event.notification.data || {};
  const receivedMs = (typeof data.receivedMs === 'number') ? data.receivedMs : Date.now();
  const qs = '?n=' + urlSafeB64Encode(JSON.stringify(data)) + '&r=' + receivedMs;
  const target = new URL(self.registration.scope);
  target.search = qs;
  event.waitUntil((async () => {
    // Stash for the page in case the PWA launcher strips query string
    // (Android Chrome normalizes to manifest start_url for installed PWAs).
    try { await idbPut('last-click', { data, receivedMs }); } catch {}
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const w of wins) {
      if (w.url.startsWith(self.registration.scope)) {
        try { w.postMessage({ type: 'show-detail', search: qs }); } catch {}
        return w.focus();
      }
    }
    return self.clients.openWindow(target.href);
  })());
});
