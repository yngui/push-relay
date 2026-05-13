'use strict';

const statusEl = document.getElementById('status');
const subEl = document.getElementById('sub');
const enableBtn = document.getElementById('enable');
const copyBtn = document.getElementById('copy');
const subscribeSection = document.getElementById('subscribe');
const detailSection = document.getElementById('detail');

function setStatus(msg, kind) { statusEl.textContent = msg; statusEl.className = kind || ''; }

function urlB64ToUint8Array(b) {
  const pad = '='.repeat((4 - b.length % 4) % 4);
  const s = (b + pad).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(s);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function urlSafeB64Decode(b) {
  const pad = '='.repeat((4 - b.length % 4) % 4);
  const s = (b + pad).replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(s);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

function fmtTime(input) {
  if (!input) return '—';
  const d = typeof input === 'number' ? new Date(input) : new Date(input);
  if (isNaN(+d)) return String(input);
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3,
  }).format(d);
}

function renderDetailFromData(data, receivedMs) {
  document.getElementById('d-title').textContent = data.title || 'Notification';
  document.getElementById('d-body').textContent = data.body || '';
  const src = (data.source && typeof data.source === 'object') ? data.source : {};
  const srcText = Object.values(src).filter(v => typeof v === 'string' && v).join(' \u00b7 ');
  document.getElementById('d-source-line').textContent = srcText;
  document.getElementById('d-sent').textContent = fmtTime(data.sent);
  document.getElementById('d-received').textContent = fmtTime(receivedMs);
  const iconEl = document.getElementById('d-icon');
  if (data.icon && (data.icon.startsWith('icons/') || data.icon.startsWith('https://') || data.icon.startsWith('data:image/'))) {
    iconEl.src = data.icon;
    iconEl.hidden = false;
  } else {
    iconEl.hidden = true;
    iconEl.removeAttribute('src');
  }
  subscribeSection.hidden = true;
  detailSection.hidden = false;
}

function renderDetailFromLocation() {
  const src = location.search || location.hash || '';
  const m = /[?#&]n=([^&]+)(?:&r=(\d+))?/.exec(src);
  if (!m) return false;
  let data;
  try {
    data = JSON.parse(urlSafeB64Decode(m[1]));
  } catch (e) {
    console.warn('detail parse failed', e);
    return false;
  }
  renderDetailFromData(data, m[2] ? Number(m[2]) : Date.now());
  return true;
}

function idbGetAndClear(key) {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('push-relay', 1);
    req.onupgradeneeded = () => req.result.createObjectStore('kv');
    req.onerror = () => reject(req.error);
    req.onsuccess = () => {
      const db = req.result;
      const tx = db.transaction('kv', 'readwrite');
      const store = tx.objectStore('kv');
      const g = store.get(key);
      g.onsuccess = () => { store.delete(key); };
      tx.oncomplete = () => { db.close(); resolve(g.result); };
      tx.onerror = () => reject(tx.error);
    };
  });
}

async function tryRenderFromIdb() {
  try {
    const v = await idbGetAndClear('last-click');
    if (!v || !v.data) return false;
    if (Date.now() - (v.receivedMs || 0) > 5 * 60 * 1000) return false;
    renderDetailFromData(v.data, v.receivedMs || Date.now());
    return true;
  } catch (e) {
    console.warn('idb read failed', e);
    return false;
  }
}

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.addEventListener('message', e => {
    if (!e.data) return;
    if (e.data.type === 'show-detail' && (e.data.search || e.data.hash)) {
      const next = e.data.search || e.data.hash;
      if (next.startsWith('?')) {
        history.replaceState(null, '', location.pathname + next);
      } else {
        location.hash = next;
      }
      renderDetailFromLocation();
    }
  });
}

// Initial dispatch: try URL first (open-in-tab case), then IDB (installed-PWA case).
(async () => {
  if (renderDetailFromLocation()) return;
  if (await tryRenderFromIdb()) return;
  subscribeSection.hidden = false;
  detailSection.hidden = true;
})();

async function subscribe() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    setStatus('Push not supported in this browser.', 'err'); return;
  }
  if (!window.VAPID_PUBLIC) {
    setStatus('VAPID_PUBLIC missing in config.js (deploy issue).', 'err'); return;
  }
  setStatus('Registering service worker…');
  const reg = await navigator.serviceWorker.register('sw.js');
  await navigator.serviceWorker.ready;

  setStatus('Requesting permission…');
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') { setStatus('Permission denied: ' + perm, 'err'); return; }

  setStatus('Subscribing…');
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlB64ToUint8Array(window.VAPID_PUBLIC)
    });
  }
  const json = JSON.stringify(sub.toJSON());
  subEl.textContent = json;
  copyBtn.disabled = false;
  setStatus('Subscribed ✓ — copy the JSON below and paste into add-sub.py on the sender.', 'ok');
}

enableBtn.addEventListener('click', () => subscribe().catch(e => setStatus('Error: ' + e.message, 'err')));
copyBtn.addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(subEl.textContent); setStatus('Copied to clipboard.', 'ok'); }
  catch (e) { setStatus('Copy failed: ' + e.message, 'err'); }
});
