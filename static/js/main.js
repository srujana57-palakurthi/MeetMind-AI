/* =========================================================================
   main.js - shared behaviour for every page
   ========================================================================= */

/** Show the full-screen loading overlay with a custom message. */
function showLoading(title, hint) {
  const overlay = document.getElementById('loadingOverlay');
  if (!overlay) return;
  const t = document.getElementById('loadingTitle');
  const h = document.getElementById('loadingHint');
  if (t && title) t.textContent = title;
  if (h && hint) h.textContent = hint;
  overlay.classList.remove('d-none');
}

function hideLoading() {
  const overlay = document.getElementById('loadingOverlay');
  if (overlay) overlay.classList.add('d-none');
}

/** Small confirmation popup (Bootstrap toast). */
function showToast(message) {
  const body = document.getElementById('appToastBody');
  const el = document.getElementById('appToast');
  if (!body || !el) { return; }
  body.textContent = message;
  bootstrap.Toast.getOrCreateInstance(el, { delay: 2500 }).show();
}

/** POST JSON and return the parsed body (throws on server errors). */
async function postJSON(url, payload) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {})
  });
  let data = {};
  try { data = await response.json(); } catch (e) { /* not JSON */ }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || ('Request failed (' + response.status + ')'));
  }
  return data;
}

/** Trigger a client-side file download (used for the email .txt). */
function downloadText(filename, text) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Copy text to the clipboard (with a fallback for older browsers). */
async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.focus();
  area.select();
  document.execCommand('copy');
  document.body.removeChild(area);
}

document.addEventListener('DOMContentLoaded', () => {
  /* auto-hide flash messages after a few seconds */
  document.querySelectorAll('.alert-dismissible').forEach((alert) => {
    setTimeout(() => {
      try { bootstrap.Alert.getOrCreateInstance(alert).close(); } catch (e) {}
    }, 7000);
  });
});
