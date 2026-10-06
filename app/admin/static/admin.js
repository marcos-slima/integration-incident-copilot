// Admin UI - funcoes compartilhadas (DA-46..50).
//
// Servido como arquivo estatico porque a CSP das paginas e
// `script-src 'self'`: <script> inline e atributos onclick/onsubmit sao
// bloqueados pelo navegador. Toda interacao usa delegacao de eventos:
//   <button data-click=NOME data-args=JSON>  -> NOME.apply(botao, args)
//   <form data-submit=NOME>                   -> NOME.call(form, evento)
// Somente funcoes globais declaradas pelos scripts da propria pagina sao
// chamadas (nome validado como identificador).

function saveKey() {
  const v = document.getElementById('adminkey').value.trim();
  if (!v) { sessionStorage.removeItem('adminKey'); return; }
  sessionStorage.setItem('adminKey', v);
  location.reload();
}

function adminFetch(path, opts) {
  opts = opts || {};
  opts.headers = Object.assign({}, opts.headers || {});
  const k = sessionStorage.getItem('adminKey');
  if (k) opts.headers['X-API-Admin-Key'] = k;
  opts.headers['Content-Type'] = 'application/json';
  return fetch(path, opts);
}

function restoreKeyLabel() {
  const k = sessionStorage.getItem('adminKey');
  const el = document.getElementById('adminkey');
  if (el && k) el.value = k;
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s == null ? '' : String(s);
  return div.innerHTML;
}

// Para valores dentro de atributos ("..."): escapeHtml nao escapa aspas.
function escapeAttr(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function pill(status) {
  const cls = status === 'active' ? 'status-active' : (status === 'degraded' ? 'status-degraded' : 'status-offline');
  return `<span class="status-badge ${cls}">${escapeHtml(status)}</span>`;
}

// Botao que dispara `fnName` (para feedback "Carregando..."). Devolve um
// elemento descartavel quando nao existe, para o chamador nao quebrar.
function actionButton(fnName) {
  return document.querySelector(`[data-click="${fnName}"]`) || document.createElement('button');
}

function _resolveHandler(name) {
  if (!/^[A-Za-z_$][\w$]*$/.test(name || '')) return null;
  const fn = window[name];
  return typeof fn === 'function' ? fn : null;
}

document.addEventListener('click', function (ev) {
  const el = ev.target.closest('[data-click]');
  if (!el) return;
  const fn = _resolveHandler(el.dataset.click);
  if (!fn) { console.error('handler inexistente:', el.dataset.click); return; }
  let args = [];
  if (el.dataset.args) {
    try { args = JSON.parse(el.dataset.args); } catch (e) { console.error('data-args invalido', e); return; }
  }
  ev.preventDefault();
  fn.apply(el, args);
});

document.addEventListener('submit', function (ev) {
  const form = ev.target.closest('form[data-submit]');
  if (!form) return;
  ev.preventDefault();
  const fn = _resolveHandler(form.dataset.submit);
  if (!fn) { console.error('handler inexistente:', form.dataset.submit); return; }
  fn.call(form, ev);
});

restoreKeyLabel();
