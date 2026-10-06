// models (Admin UI) - extraido do template para respeitar a CSP (script-src 'self').
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
