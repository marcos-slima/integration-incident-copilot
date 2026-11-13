// Admin UI - funcoes compartilhadas (DA-46..50).
//
// Servido como arquivo estatico porque a CSP das paginas e
// `script-src 'self'`: <script> inline e atributos onclick/onsubmit sao
// bloqueados pelo navegador. Toda interacao usa delegacao de eventos:
//   <button data-click=NOME data-args=JSON>  -> NOME.apply(botao, args)
//   <form data-submit=NOME>                   -> NOME.call(form, evento)
// Somente funcoes globais declaradas pelos scripts da propria pagina sao
// chamadas (nome validado como identificador).

// ---------------------------------------------------------------------------
// DA-73: i18n. O catalogo do idioma resolvido pelo servidor (cookie
// iic_locale -> Accept-Language -> en) vem embutido em
// <script type="application/json" id="i18n-catalog"> (dado, nao script: a
// CSP script-src 'self' continua valendo). Mesma sintaxe do i18next:
// {{var}}, plural por sufixo _zero/_one/_other.
// ---------------------------------------------------------------------------

const I18N = (function () {
  try { return JSON.parse(document.getElementById('i18n-catalog').textContent); }
  catch (e) { return {}; }
})();
const I18N_LANG = document.documentElement.lang || 'en';

function _i18nLookup(key) {
  return key.split('.').reduce((node, part) => (node && typeof node === 'object' ? node[part] : undefined), I18N);
}

function t(key, vars) {
  vars = vars || {};
  let text;
  if (typeof vars.count === 'number') {
    if (vars.count === 0) text = _i18nLookup(key + '_zero');
    if (text == null) text = _i18nLookup(key + '_' + new Intl.PluralRules(I18N_LANG).select(vars.count));
    if (text == null) text = _i18nLookup(key + '_other');
  }
  if (text == null) text = _i18nLookup(key);
  if (typeof text !== 'string') return key;
  return text.replace(/\{\{\s*(\w+)\s*\}\}/g, (m, name) => (name in vars ? String(vars[name]) : m));
}

// Erro da API: `code` estavel (DA-73, F5) vira texto do catalogo; sem code
// conhecido, o `detail` do backend como esta.
function apiErrorText(body, status) {
  if (body && body.code && typeof _i18nLookup('errors.' + body.code) === 'string') {
    return t('errors.' + body.code, Object.assign({}, body.params || {}));
  }
  if (body && typeof body.detail === 'string') return body.detail;
  if (body && body.detail) return JSON.stringify(body.detail);
  return t('common.httpError', {status: status});
}

function fmtNumber(n, digits) {
  return new Intl.NumberFormat(I18N_LANG, {maximumFractionDigits: digits == null ? 2 : digits}).format(n);
}

function fmtUSD(n, digits) {
  const d = digits == null ? 2 : digits;
  return new Intl.NumberFormat(I18N_LANG, {style: 'currency', currency: 'USD', minimumFractionDigits: d, maximumFractionDigits: d}).format(n);
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d) ? iso : new Intl.DateTimeFormat(I18N_LANG, {dateStyle: 'short', timeStyle: 'medium'}).format(d);
}

// Seletor de idioma do cabecalho: grava o cookie compartilhado com a UI web.
document.addEventListener('change', function (ev) {
  if (ev.target && ev.target.id === 'lang-select') {
    document.cookie = 'iic_locale=' + encodeURIComponent(ev.target.value) + '; path=/; max-age=31536000; SameSite=Lax';
    const url = new URL(location.href);
    url.searchParams.delete('lng');
    location.replace(url.toString());
  }
});

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

// Rotulo de um codigo de status (active, degraded...) no idioma da pagina;
// codigo desconhecido aparece como veio.
function statusLabel(code) {
  return typeof _i18nLookup('statusLabel.' + code) === 'string' ? t('statusLabel.' + code) : code;
}

function pill(status) {
  const cls = status === 'active' ? 'status-active' : (status === 'degraded' ? 'status-degraded' : 'status-offline');
  return `<span class="status-badge ${cls}" title="${escapeAttr(status)}">${escapeHtml(statusLabel(status))}</span>`;
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
