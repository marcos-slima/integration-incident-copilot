// models (Admin UI) - extraido do template para respeitar a CSP (script-src 'self').

// ---------------------------------------------------------------------------
// Runtime config overrides
// ---------------------------------------------------------------------------

function loadRuntimeConfig() {
  const btn = actionButton('loadRuntimeConfig');
  const originalText = btn.textContent;
  btn.textContent = 'Carregando...';
  btn.disabled = true;

  adminFetch('/admin/api/runtime-config').then(r => {
    if (r.status === 401) throw new Error('Chave admin inválida — insira acima');
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
    ];

    for (const f of fields) {
      const val = cfg[f.key];
      const tr = document.createElement('tr');

      if (f.type === 'toggle') {
        const displayVal = val ? '<span class="status-badge status-active">Sim</span>' : '<span class="status-badge status-degraded">Não</span>';
        tr.innerHTML = `
          <td style="font-family: monospace; font-size: 0.9rem;">${f.label}</td>
          <td id="rc-${f.key}">${displayVal}</td>
          <td>
            <button data-click="toggleRuntimeConfig" data-args="${escapeAttr(JSON.stringify([f.key, !val]))}" class="btn" style="padding: 0.4rem 0.8rem; background: var(--border);">🔄 Alternar</button>
          </td>`;
      } else if (f.type === 'select') {
        const displayVal = val ? escapeHtml(val) : '<em style="opacity:0.5">(vazio)</em>';
        const options = (f.options || []).map(opt =>
          `<option value="${escapeAttr(opt)}" ${val === opt ? 'selected' : ''}>${opt || '(vazio)'}</option>`
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
        const displayVal = val ?? '<em style="opacity:0.5">(vazio)</em>';
        tr.innerHTML = `
          <td style="font-family: monospace; font-size: 0.9rem;">${f.label}</td>
          <td id="rc-${f.key}" style="font-family: monospace;">${displayVal}</td>
          <td>
            <button data-click="editRuntimeConfig" data-args="${escapeAttr(JSON.stringify([f.key, val]))}" class="btn" style="padding: 0.4rem 0.8rem; background: var(--border);">✏️ Editar</button>
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

  const diffFields = ['llm_registry_db', 'llm_provider', 'llm_route', 'llm_fallback_provider'];
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
    if (r.status === 401) throw new Error('Chave admin inválida — insira acima');
    return r.json();
  }).then(data => {
    const warnings = data.warnings || [];
    if (warnings.length > 0) {
      const msg = document.getElementById('msg');
      msg.innerHTML = '<strong>Atenção antes de ativar LLM_REGISTRY_DB:</strong><br>' + warnings.map(w => '• ' + escapeHtml(w)).join('<br>');
      msg.style.display = 'block';
    } else {
      alert('Tudo certo! ' + data.enabled_models_count + ' modelo(s) habilitado(s) para ' + data.provider);
    }
  }).catch(e => {
    document.getElementById('msg').textContent = 'Erro: ' + e.message;
  });
}

function saveRuntimeConfig(key, value) {
  // Converter vazio string → null para campos de texto
  const sendValue = value === '' ? null : value;
  adminFetch('/admin/api/runtime-config', {method:'PATCH', body: JSON.stringify({[key]: sendValue})})
    .then(r => r.ok ? loadRuntimeConfig() : r.json().then(j => { throw new Error(j.detail); }))
    .catch(e => document.getElementById('msg').textContent = 'Erro: ' + e.message);
}

function toggleRuntimeConfig(key, target) {
  if (!confirm(target ? `Ligar ${key}?` : `Desligar ${key}?`)) return;
  adminFetch('/admin/api/runtime-config', {method:'PATCH', body: JSON.stringify({[key]: target})})
    .then(r => r.ok ? loadRuntimeConfig() : r.json().then(j => { throw new Error(j.detail); }))
    .catch(e => document.getElementById('msg').textContent = 'Erro: ' + e.message);
}

function editRuntimeConfig(key, currentVal) {
  const label = key.replace('llm_', '').replace('_', ' ').toUpperCase();
  const newVal = prompt(`Novo valor para ${label} (atual: ${currentVal ?? '(vazio)'})`);
  if (newVal === null) return; // cancelado
  adminFetch('/admin/api/runtime-config', {method:'PATCH', body: JSON.stringify({[key]: newVal || null})})
    .then(r => r.ok ? loadRuntimeConfig() : r.json().then(j => { throw new Error(j.detail); }))
    .catch(e => document.getElementById('msg').textContent = 'Erro: ' + e.message);
}

// ---------------------------------------------------------------------------
// Modelos
// ---------------------------------------------------------------------------

function loadModels() {
  const btn = actionButton('loadModels');
  const originalText = btn.textContent;
  btn.textContent = 'Carregando...';
  btn.disabled = true;

  adminFetch('/admin/api/models').then(r => {
    if (r.status === 401) throw new Error('Chave admin inválida — insira acima');
    if (r.status === 503) throw new Error('DB não configurado (DATABASE_URL ausente)');
    return r.json();
  }).then(rows => {
    const tb = document.getElementById('models-body');
    tb.innerHTML = '';
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="10" style="text-align: center; padding: 2rem; color: var(--text); opacity: 0.7;">Nenhum modelo registrado</td></tr>';
      btn.textContent = originalText;
      btn.disabled = false;
      return;
    }
    for (const m of rows) {
      const pct = m.usage.percent == null ? '—' : m.usage.percent + '%';
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
        <td>${m.enabled ? '<span class="status-badge status-active">Sim</span>' : '<span class="status-badge status-degraded">Não</span>'}</td>
        <td>
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
  const key = prompt('Chave de API para ' + origin + ' (grava CIFRADA, DA-47):');
  if (!key) return;
  adminFetch('/admin/api/credentials/' + encodeURIComponent(origin), {method:'PUT', body: JSON.stringify({key})})
    .then(r => r.json())
    .then(() => loadModels())
    .catch(e => document.getElementById('msg').textContent = 'Erro na chave: ' + e.message);
}

function toggleModel(id, target) {
  if (!confirm(target ? 'Ativar este modelo?' : 'Desativar este modelo?')) return;
  adminFetch('/admin/api/models/' + id, {method:'PATCH', body: JSON.stringify({enabled: target})})
    .then(() => loadModels()).catch(e => document.getElementById('msg').textContent = e.message);
}

function delModel(id) {
  if (!confirm('Remover este modelo permanentemente?')) return;
  adminFetch('/admin/api/models/' + id, {method:'DELETE'})
    .then(() => loadModels()).catch(e => document.getElementById('msg').textContent = e.message);
}

function addModel(e) {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = Object.fromEntries([...f.entries()].map(([k,v]) => [
    k, (k === 'is_default') ? !!v : (v === '' ? null : v)
  ]));

  const btn = e.target.querySelector('button[type="submit"]');
  const originalText = btn.textContent;
  btn.textContent = 'Adicionando...';
  btn.disabled = true;

  adminFetch('/admin/api/models', {method:'POST', body: JSON.stringify(body)})
    .then(r => r.ok ? loadModels() : r.json().then(j => { throw new Error(j.detail); }))
    .then(() => e.target.reset())
    .catch(err => document.getElementById('msg').textContent = err.message)
    .finally(() => { btn.textContent = originalText; btn.disabled = false; });

  return false;
}

loadModels();
