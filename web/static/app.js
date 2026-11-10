// Shared page helpers: copy buttons, the "working" overlay, file pickers, the sample-fill link, and toasts.

function toast(message) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 2600);
}

// The app's own "are you sure?" window, in place of the browser's confirm(). Resolves true for OK; Cancel, Esc and a
// click outside the window all resolve false.
function confirmModal({ title, body = '', ok = 'OK', cancel = 'Cancel' }) {
  const dialog = document.getElementById('confirm-modal');
  document.getElementById('confirm-title').textContent = title;
  document.getElementById('confirm-body').textContent = body;
  document.getElementById('confirm-ok').textContent = ok;
  document.getElementById('confirm-cancel').textContent = cancel;
  dialog.returnValue = 'cancel';
  dialog.showModal();
  document.getElementById('confirm-ok').focus();
  return new Promise((resolve) => {
    const outside = (e) => { if (e.target === dialog) dialog.close('cancel'); };
    dialog.addEventListener('click', outside);
    dialog.addEventListener('close', () => {
      dialog.removeEventListener('click', outside);
      resolve(dialog.returnValue === 'ok');
    }, { once: true });
  });
}

function showOverlay(message) {
  document.getElementById('overlay-text').textContent = message;
  document.getElementById('overlay').hidden = false;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = Object.assign(document.createElement('textarea'), { value: text });
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }
  toast('Copied');
}

document.addEventListener('click', (event) => {
  const copy = event.target.closest('[data-copy]');
  if (copy) {
    const source = document.querySelector(copy.dataset.copy);
    const lines = source.matches('ol, ul') ? [...source.children].map((li, i) => `${i + 1}. ${li.textContent.trim()}`)
      : [source.matches('input, textarea') ? source.value.trim() : source.textContent.trim()]; // a field copies what was typed
    copyText(lines.join('\n'));
  }
  // Opens a Gmail draft (the district is on Google) with the subject and message as they stand, edits included.
  const gmail = event.target.closest('[data-gmail]');
  if (gmail) {
    const part = (key, value) => `${key}=${encodeURIComponent(value)}`;
    const query = [part('view', 'cm'), part('fs', '1'), part('to', gmail.dataset.to || ''),
      part('su', document.querySelector(gmail.dataset.subject).value), part('body', document.querySelector(gmail.dataset.body).value)];
    window.open(`https://mail.google.com/mail/?${query.join('&')}`, '_blank', 'noopener');
  }
  const fill = event.target.closest('[data-fill]');
  if (fill) {
    const target = document.querySelector(fill.dataset.fill);
    target.value = document.querySelector(fill.dataset.fillFrom).content.textContent.trim();
    target.focus();
  }
});

// Long server steps (the AI drafting a plan) show a message instead of a frozen page.
document.addEventListener('submit', (event) => {
  const form = event.target;
  if (form.dataset.wait && form.checkValidity()) {
    showOverlay(form.dataset.wait);
  }
});

// File pickers show what was chosen; drop zones accept dragged files.
document.querySelectorAll('input[type=file][data-file-label]').forEach((input) => {
  const label = document.querySelector(input.dataset.fileLabel);
  const zone = input.closest('.dropzone');
  const show = () => {
    if (input.files.length) label.textContent = [...input.files].map((f) => f.name).join(', ');
  };
  input.addEventListener('change', show);
  ['dragenter', 'dragover'].forEach((type) => zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.add('is-over'); }));
  ['dragleave', 'drop'].forEach((type) => zone.addEventListener(type, () => zone.classList.remove('is-over')));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    input.files = e.dataTransfer.files;
    show();
  });
});
