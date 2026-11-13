// models (Admin UI) - extraido do template para respeitar a CSP (script-src 'self').

// ---------------------------------------------------------------------------
// Runtime config overrides
// ---------------------------------------------------------------------------

function loadRuntimeConfig() {
  const btn = actionButton('loadRuntimeConfig');
  const originalText = btn.textContent;
  btn.textContent = t('common.loading');
  btn.disabled = true;

  adminFetch('/admin/api/runtime-config').then(r => {
    if (r.status === 401) throw new Error(t('common.invalidKey'));
    return r.json();
  }).then(cfg => {
    const tb = document.getElementById('runtime-config-body');
    tb.innerHTML = '';

    // GAP-3: banner quando DB != .env
    showRuntimeDiffBanner(cfg);

    const fields = [
      { key: 'llm_registry_db', label: 'LLM_REGISTRY_DB', type: 'toggle' },
      { key: 'llm_provider',    label: 'LLM_PROVIDER',    type: 'text' },
      { key: 'llm_route',       label: 'LLM_ROUTE',       type: 'text' },
      { key: 'llm_fallback_provider', label: 'LLM_FALLBACK_PROVIDER', type: 'select', options: ['', 'ollama', 'openai', 'azure_openai'] },
      { key: 'llm_credentials_master_key', label: 'LLM_CREDENTIALS_MASTER_KEY', type: 'text' },
      { key: 'data_sovereignty_mode', label: 'DATA_SOVEREIGNTY_MODE', type: 'select', options: ['', 'strict', 'cloud_with_dlp'] },
      { key: 'confidential_allowed_origins', label: 'CONFIDENTIAL_ALLOWED_ORIGINS', type: 'text' },
      { key: 'require_auth', label: 'REQUIRE_AUTH', type: 'toggle' },
      { key: 'rule_engine_enabled', label: 'RULE_ENGINE_ENABLED', type: 'toggle' },
      { key: 'diagnosis_timeout_seconds', label: 'DIAGNOSIS_TIMEOUT_SECONDS', type: 'text' },
      { key: 'llm_request_timeout_seconds', label: 'LLM_REQUEST_TIMEOUT_SECONDS', type: 'text' },
      // DA-72: orçamento (DA-67) e pesos do score de roteamento
      { key: 'diagnosis_budget_usd', label: 'DIAGNOSIS_BUDGET_USD', type: 'text' },
      { key: 'llm_budget_reserve_ratio', label: 'LLM_BUDGET_RESERVE_RATIO', type: 'text' },
      { key: 'llm_gateway_max_cost_usd', label: 'LLM_GATEWAY_MAX_COST_USD', type: 'text' },
      { key: 'llm_routing_cost_weight', label: 'LLM_ROUTING_COST_WEIGHT', type: 'text' },
      { key: 'llm_routing_latency_weight', label: 'LLM_ROUTING_LATENCY_WEIGHT', type: 'text' },
    ];

    for (const f of fields) {
      const val = cfg[f.key];
      const tr = document.createElement('tr');

      if (f.type === 'toggle') {
        const displayVal = val ? `<span class="status-badge status-active">${escapeHtml(t('common.yes'))}</span>` : `<span class="status-badge status-degraded">${escapeHtml(t('common.no'))}</span>`;
        tr.innerHTML = `
          <td style="font-family: monospace; font-size: 0.9rem;">${f.label}</td>
          <td id="rc-${f.key}">${displayVal}</td>
          <td>
            <button data-click="toggleRuntimeConfig" data-args="${escapeAttr(JSON.stringify([f.key, !val]))}" class="btn" style="padding: 0.4rem 0.8rem; background: var(--border);">🔄 ${escapeHtml(t('models.toggle'))}</button>
          </td>`;
      } else if (f.type === 'select') {
        const displayVal = val ? escapeHtml(val) : `<em style="opacity:0.5">${escapeHtml(t('common.empty'))}</em>`;
        const options = (f.options || []).map(opt =>
          `<option value="${escapeAttr(opt)}" ${val === opt ? 'selected' : ''}>${escapeHtml(opt || t('common.empty'))}</option>`
        ).join('');
        tr.innerHTML = `
          <td style="font-family: monospace; font-size: 0.9rem;">${f.label}</td>
          <td id="rc-${f.key}" style="font-family: monospace;">${displayVal}</td>
          <td>
            <select data-runtime-key="${f.key}" style="padding: 0.3rem 0.5rem; font-family: monospace; font-size: 0.85rem;">
              ${options}
            </select>
          </td>`;
        // GAP-7: listener via JS (CSP bloqueia inline handlers)
        const sel = tr.querySelector('select');
        sel.addEventListener('change', function() {
          saveRuntimeConfig(f.key, this.value);
        });
      } else {
        const displayVal = val == null ? `<em style="opacity:0.5">${escapeHtml(t('common.empty'))}</em>` : escapeHtml(val);
        tr.innerHTML = `
          <td style="font-family: monospace; font-size: 0.9rem;">${f.label}</td>
          <td id="rc-${f.key}" style="font-family: monospace;">${displayVal}</td>
          <td>
            <button data-click="editRuntimeConfig" data-args="${escapeAttr(JSON.stringify([f.key, val]))}" class="btn" style="padding: 0.4rem 0.8rem; background: var(--border);">✏️ ${escapeHtml(t('common.edit'))}</button>
          </td>`;
      }

      tb.appendChild(tr);
    }
    btn.textContent = originalText;
    btn.disabled = false;
  }).catch(e => {
    const msg = document.getElementById('msg');
    msg.textContent = e.message;
    msg.style.display = 'block';
    btn.textContent = originalText;
    btn.disabled = false;
  });
}

// GAP-3: mostrar banner quando valores do DB diferem do .env
function showRuntimeDiffBanner(cfg) {
  const envValues = cfg.env_values;
  const banner = document.getElementById('runtime-diff-banner');
  if (!envValues) {
    banner.style.display = 'none';
    return;
  }

  const diffFields = ['llm_registry_db', 'llm_provider', 'llm_route', 'llm_fallback_provider', 'llm_credentials_master_key', 'data_sovereignty_mode', 'confidential_allowed_origins', 'require_auth', 'rule_engine_enabled', 'diagnosis_timeout_seconds', 'llm_request_timeout_seconds', 'diagnosis_budget_usd', 'llm_budget_reserve_ratio', 'llm_gateway_max_cost_usd', 'llm_routing_cost_weight', 'llm_routing_latency_weight'];
  let hasDiff = false;
  for (const key of diffFields) {
    if (String(cfg[key]) !== String(envValues[key])) {
      hasDiff = true;
      break;
    }
  }

  banner.style.display = hasDiff ? 'block' : 'none';
}

// GAP-2: pré-check antes de ativar registry
function checkActivateRegistry() {
  adminFetch('/admin/api/runtime-config/check-activate').then(r => {
    if (r.status === 401) throw new Error(t('common.invalidKey'));
    return r.json();
  }).then(data => {
    const warnings = data.warnings || [];
    if (warnings.length > 0) {
      const msg = document.getElementById('msg');
      msg.innerHTML = `<strong>${escapeHtml(t('models.activateWarning'))}</strong><br>` + warnings.map(w => '• ' + escapeHtml(w)).join('<br>');
      msg.style.display = 'block';
    } else {
      alert(t('models.activateOk', {count: data.enabled_models_count, provider: data.provider}));
    }
  }).catch(e => {
    document.getElementById('msg').textContent = t('common.error', {message: e.message});
  });
}

function saveRuntimeConfig(key, value) {
  // Converter vazio string → null para campos de texto
  const sendValue = value === '' ? null : value;
  adminFetch('/admin/api/runtime-config', {method:'PATCH', body: JSON.stringify({[key]: sendValue})})
    .then(r => r.ok ? loadRuntimeConfig() : r.json().then(j => { throw new Error(apiErrorText(j, r.status)); }))
    .catch(e => document.getElementById('msg').textContent = t('common.error', {message: e.message}));
}

function toggleRuntimeConfig(key, target) {
  if (!confirm(target ? t('models.confirmOn', {key: key}) : t('models.confirmOff', {key: key}))) return;
  adminFetch('/admin/api/runtime-config', {method:'PATCH', body: JSON.stringify({[key]: target})})
    .then(r => r.ok ? loadRuntimeConfig() : r.json().then(j => { throw new Error(apiErrorText(j, r.status)); }))
    .catch(e => document.getElementById('msg').textContent = t('common.error', {message: e.message}));
}

// Campos numéricos do runtime-config: o PATCH valida número (Pydantic).
const NUMERIC_RUNTIME_KEYS = new Set([
  'diagnosis_timeout_seconds', 'llm_request_timeout_seconds', 'diagnosis_budget_usd',
  'llm_budget_reserve_ratio', 'llm_gateway_max_cost_usd', 'llm_routing_cost_weight',
  'llm_routing_latency_weight',
]);

function editRuntimeConfig(key, currentVal) {
  const label = key.toUpperCase();
  const newVal = prompt(t('models.promptValue', {key: label, current: currentVal ?? t('common.empty')}));
  if (newVal === null) return; // cancelado
  let value = newVal.trim() || null;
  if (value !== null && NUMERIC_RUNTIME_KEYS.has(key)) {
    value = Number(value.replace(',', '.'));
    if (!Number.isFinite(value)) { document.getElementById('msg').textContent = t('common.invalidNumber'); return; }
  }
  adminFetch('/admin/api/runtime-config', {method:'PATCH', body: JSON.stringify({[key]: value})})
    .then(r => r.ok ? loadRuntimeConfig() : r.json().then(j => { throw new Error(apiErrorText(j, r.status)); }))
    .catch(e => document.getElementById('msg').textContent = t('common.error', {message: e.message}));
}

// GAP-A: Exportar configuração efetiva
function exportEffectiveConfig() {
  const btn = actionButton('exportEffectiveConfig');
  const originalText = btn.textContent;
  btn.textContent = t('models.exporting');
  btn.disabled = true;

  adminFetch('/admin/api/effective-config').then(r => {
    if (r.status === 401) throw new Error(t('common.invalidKey'));
    return r.json();
  }).then(cfg => {
    const preview = document.getElementById('effective-config-preview');
    const jsonStr = JSON.stringify(cfg, null, 2);
    preview.textContent = jsonStr;
    preview.style.display = 'block';

    // Copiar para a área de transferência
    navigator.clipboard.writeText(jsonStr).then(() => {
      alert(t('models.copied'));
    }).catch(() => {
      // Fallback: selecionar e copiar manualmente
      preview.select();
      document.execCommand('copy');
      alert(t('models.copied'));
    });
  }).catch(e => {
    document.getElementById('msg').textContent = t('common.error', {message: e.message});
  }).finally(() => {
    btn.textContent = originalText;
    btn.disabled = false;
  });
}

// ---------------------------------------------------------------------------
// Modelos
// ---------------------------------------------------------------------------

function loadModels() {
  const btn = actionButton('loadModels');
  const originalText = btn.textContent;
  btn.textContent = t('common.loading');
  btn.disabled = true;

  adminFetch('/admin/api/models').then(r => {
    if (r.status === 401) throw new Error(t('common.invalidKey'));
    if (r.status === 503) throw new Error(t('common.dbMissing'));
    return r.json();
  }).then(rows => {
    const tb = document.getElementById('models-body');
    tb.innerHTML = '';
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="11" style="text-align: center; padding: 2rem; color: var(--text); opacity: 0.7;">' + escapeHtml(t('models.empty')) + '</td></tr>';
      btn.textContent = originalText;
      btn.disabled = false;
      return;
    }
    MODELS_BY_ID = Object.fromEntries(rows.map(m => [m.id, m]));
    for (const m of rows) {
      const pct = m.usage.percent == null ? '—' : fmtNumber(m.usage.percent, 1) + '%';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(m.provider_origin)}</td>
        <td style="font-weight: 500;"><span style="font-size: 0.85rem; opacity: 0.6; margin-right: 0.25rem;">🔑</span>${escapeHtml(m.model_id)}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(m.base_url || '—')}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${m.price_in_per_1m ?? '—'}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${m.price_out_per_1m ?? '—'}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${m.monthly_limit_tokens ?? '—'}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${pct}</td>
        <td>${m.is_default ? '<span style="color: var(--success);">✓</span>' : '—'}</td>
        <td>${m.enabled ? `<span class="status-badge status-active">${escapeHtml(t('common.yes'))}</span>` : `<span class="status-badge status-degraded">${escapeHtml(t('common.no'))}</span>`}</td>
        <td style="font-size: 0.85rem;">${routingSummary(m)}</td>
        <td>
          <button data-click="editModel" data-args="${escapeAttr(JSON.stringify([m.id]))}" class="btn" style="padding: 0.4rem 0.8rem; background: var(--border);" title="${escapeAttr(t('common.edit'))}">✏️</button>
          <button data-click="setCredential" data-args="${escapeAttr(JSON.stringify([m.provider_origin]))}" class="btn" style="padding: 0.4rem 0.8rem; background: var(--border);">🔐</button>
          <button data-click="toggleModel" data-args="${escapeAttr(JSON.stringify([m.id, !m.enabled]))}" class="btn" style="padding: 0.4rem 0.8rem; background: var(--border);">🔄</button>
          <button data-click="delModel" data-args="${escapeAttr(JSON.stringify([m.id]))}" class="btn btn-danger" style="padding: 0.4rem 0.8rem;">🗑️</button>
        </td>`;
      tb.appendChild(tr);
    }
    btn.textContent = originalText;
    btn.disabled = false;
  }).catch(e => {
    const msg = document.getElementById('msg');
    msg.textContent = e.message;
    msg.style.display = 'block';
    btn.textContent = originalText;
    btn.disabled = false;
  });
}

function setCredential(origin) {
  const key = prompt(t('models.promptKey', {origin: origin}));
  if (!key) return;
  adminFetch('/admin/api/credentials/' + encodeURIComponent(origin), {method:'PUT', body: JSON.stringify({key})})
    .then(r => r.json())
    .then(() => loadModels())
    .catch(e => document.getElementById('msg').textContent = t('models.keyError', {message: e.message}));
}

function toggleModel(id, target) {
  if (!confirm(target ? t('models.confirmEnable') : t('models.confirmDisable'))) return;
  adminFetch('/admin/api/models/' + id, {method:'PATCH', body: JSON.stringify({enabled: target})})
    .then(() => loadModels()).catch(e => document.getElementById('msg').textContent = e.message);
}

function delModel(id) {
  if (!confirm(t('models.confirmDelete'))) return;
  adminFetch('/admin/api/models/' + id, {method:'DELETE'})
    .then(() => loadModels()).catch(e => document.getElementById('msg').textContent = e.message);
}

// ---------------------------------------------------------------------------
// DA-72: parâmetros de roteamento por modelo
// ---------------------------------------------------------------------------

let MODELS_BY_ID = {};

function routingSummary(m) {
  const parts = [];
  parts.push(escapeHtml(m.transport || t('models.sumTransport')));
  parts.push(escapeHtml(m.context_window_tokens ? t('models.sumCtx', {k: fmtNumber(Math.round(m.context_window_tokens / 1000), 0)}) : t('models.sumCtxFree')));
  const noCaps = ['tools', 'response_format', 'seed'].filter(c => m['supports_' + c] === false);
  if (noCaps.length) parts.push(escapeHtml(t('models.sumNoCaps', {caps: noCaps.join('/')})));
  parts.push(escapeHtml(m.domains && m.domains.length ? m.domains.join(',') : t('models.sumAllDomains')));
  parts.push(escapeHtml(m.tier || t('models.sumTier')));
  parts.push('p' + escapeHtml(m.priority ?? 100));
  return parts.join(' · ');
}

const ROUTING_FIELDS = ['transport', 'context_window_tokens', 'tier', 'priority',
  'supports_tools', 'supports_response_format', 'supports_seed'];

function _modelBody(form, editing) {
  const f = new FormData(form);
  const body = {};
  for (const key of ['provider_origin', 'model_id']) if (!editing) body[key] = (f.get(key) || '').trim();
  body.base_url = (f.get('base_url') || '').trim() || null;
  for (const key of ['price_in_per_1m', 'price_out_per_1m']) {
    const v = f.get(key); body[key] = v === '' || v == null ? null : Number(v);
  }
  for (const key of ['monthly_limit_tokens', 'context_window_tokens']) {
    const v = f.get(key); body[key] = v === '' || v == null ? null : parseInt(v, 10);
  }
  const prio = f.get('priority');
  if (prio !== '' && prio != null) body.priority = parseInt(prio, 10);
  body.transport = f.get('transport') || null;
  body.tier = f.get('tier') || null;
  for (const key of ['supports_tools', 'supports_response_format', 'supports_seed']) {
    body[key] = f.get(key) === 'false' ? false : null;
  }
  const domains = f.getAll('domains');
  body.domains = domains.length ? domains : null;
  body.is_default = !!f.get('is_default');
  return body;
}

function saveModel(e) {
  const form = this;
  const id = form.querySelector('[name=id]').value;
  const editing = !!id;
  const btn = form.querySelector('#model-submit');
  const originalText = btn.textContent;
  btn.textContent = editing ? t('common.saving') : t('common.adding');
  btn.disabled = true;

  const req = editing
    ? adminFetch('/admin/api/models/' + encodeURIComponent(id), {method: 'PATCH', body: JSON.stringify(_modelBody(form, true))})
    : adminFetch('/admin/api/models', {method: 'POST', body: JSON.stringify(_modelBody(form, false))});
  req.then(r => r.ok ? null : r.json().then(j => { throw new Error(apiErrorText(j, r.status)); }))
    .then(() => { resetModelForm(); loadModels(); })
    .catch(err => { const msg = document.getElementById('msg'); msg.textContent = err.message; msg.style.display = 'block'; })
    .finally(() => { btn.textContent = originalText; btn.disabled = false; });
  return false;
}

function editModel(id) {
  const m = MODELS_BY_ID[id];
  if (!m) return;
  const form = document.getElementById('newmodel');
  form.querySelector('[name=id]').value = m.id;
  const set = (name, value) => { const el = form.querySelector(`[name=${name}]`); if (el) el.value = value == null ? '' : value; };
  set('provider_origin', m.provider_origin);
  set('model_id', m.model_id);
  form.querySelector('[name=provider_origin]').disabled = true;
  form.querySelector('[name=model_id]').disabled = true;
  set('base_url', m.base_url);
  set('price_in_per_1m', m.price_in_per_1m);
  set('price_out_per_1m', m.price_out_per_1m);
  set('monthly_limit_tokens', m.monthly_limit_tokens);
  for (const key of ROUTING_FIELDS) {
    if (key.startsWith('supports_')) set(key, m[key] === false ? 'false' : '');
    else set(key, m[key]);
  }
  form.querySelectorAll('[name=domains]').forEach(cb => { cb.checked = (m.domains || []).includes(cb.value); });
  form.querySelector('[name=is_default]').checked = !!m.is_default;
  document.getElementById('model-form-title').textContent = t('models.editTitle', {model: m.model_id});
  document.getElementById('model-submit').textContent = '💾 ' + t('models.saveChanges');
  document.getElementById('model-cancel').style.display = 'inline-block';
  form.scrollIntoView({behavior: 'smooth'});
}

function resetModelForm() {
  const form = document.getElementById('newmodel');
  form.reset();
  form.querySelector('[name=id]').value = '';
  form.querySelector('[name=provider_origin]').disabled = false;
  form.querySelector('[name=model_id]').disabled = false;
  document.getElementById('model-form-title').textContent = t('models.newTitle');
  document.getElementById('model-submit').textContent = '➕ ' + t('models.add');
  document.getElementById('model-cancel').style.display = 'none';
}

// Motivos de rejeicao do Policy Engine (RejectionReason, codigos estaveis)
function reasonLabel(code) {
  return typeof _i18nLookup('reasons.' + code) === 'string' ? t('reasons.' + code) : code;
}

// DA-74: o motivo de cada rejeicao vem como (codigo, parametros) e e'
// montado no idioma da tela; sem detail_items (backend antigo), o texto cru.
function detailText(c) {
  if (!Array.isArray(c.detail_items)) return (c.detail || []).join(' · ');
  return c.detail_items.map(item => {
    const p = item.params || {};
    if (item.reason === 'soberania_bloqueada' && p.code) return t('sovereigntyReason.' + p.code, p);
    const key = 'reasonDetail.' + item.reason;
    return typeof _i18nLookup(key) === 'string' ? t(key, p) : reasonLabel(item.reason);
  }).join(' · ');
}

function previewRouting(e) {
  const f = new FormData(this);
  const params = new URLSearchParams({sensitivity: f.get('sensitivity'), tokens: f.get('tokens') || '4000'});
  if (f.get('domain')) params.set('domain', f.get('domain'));
  adminFetch('/admin/api/routing/preview?' + params.toString()).then(r => {
    if (r.status === 401) throw new Error(t('common.invalidKey'));
    if (!r.ok) return r.json().then(j => { throw new Error(apiErrorText(j, r.status)); });
    return r.json();
  }).then(d => {
    const mode = d.mode === 'registry' ? t('models.simModeRegistry') : t('models.simModeEnv');
    const chosen = d.all_rejected
      ? `<strong style="color: var(--error);">${escapeHtml(t('models.simNone'))}</strong>`
      : escapeHtml(t('models.simChosen', {model: ''})) + `<strong>${escapeHtml(d.candidates[0].model_id || d.selected_provider)}</strong>`;
    document.getElementById('preview-summary').innerHTML =
      escapeHtml(t('models.simSummary', {mode: mode, budget: fmtUSD(d.budget_remaining_usd, 4), cost: fmtNumber(d.weights.cost), latency: fmtNumber(d.weights.latency)})) + ' · ' + chosen;
    const tb = document.getElementById('preview-body');
    tb.innerHTML = '';
    for (const c of d.candidates) {
      const tr = document.createElement('tr');
      const status = c.allowed
        ? `<span class="status-badge status-active">${escapeHtml(t('models.eligible'))}</span>`
        : c.reasons.map(r => `<span class="status-badge status-offline" style="margin: 0 0.2rem 0.2rem 0;" title="${escapeAttr(r)}">${escapeHtml(reasonLabel(r))}</span>`).join('')
          + `<div style="font-size: 0.8rem; opacity: 0.8;">${escapeHtml(detailText(c))}</div>`;
      tr.innerHTML = `
        <td>${c.rank ?? '—'}</td>
        <td style="font-weight: 500;">${escapeHtml(c.model_id || c.provider)}</td>
        <td style="font-family: monospace; font-size: 0.85rem;">${escapeHtml(c.origin)} <span style="opacity: 0.6;">(${escapeHtml(t('models.locality.' + c.locality))})</span></td>
        <td>${escapeHtml(c.tier || '—')}</td>
        <td style="font-family: monospace;">${escapeHtml(fmtUSD(Number(c.estimated_cost_usd), 4))}</td>
        <td style="font-family: monospace;">${c.latency_ms == null ? '—' : escapeHtml(fmtNumber(c.latency_ms, 0) + ' ms')}</td>
        <td style="font-family: monospace;">${c.score == null ? '—' : escapeHtml(fmtNumber(c.score, 3))}</td>
        <td>${status}</td>`;
      tb.appendChild(tr);
    }
    document.getElementById('preview-table').style.display = 'table';
  }).catch(err => {
    const s = document.getElementById('preview-summary');
    s.textContent = t('common.error', {message: err.message});
  });
  return false;
}

loadModels();
