// systems (Admin UI) - extraido do template para respeitar a CSP (script-src 'self').
function loadSystems() {
  const btn = actionButton('loadSystems');
  const originalText = btn.textContent;
  btn.textContent = 'Carregando...';
  btn.disabled = true;

  adminFetch('/admin/api/systems').then(r => {
    if (r.status === 401) throw new Error('Chave admin inválida — insira acima');
    if (r.status === 503) throw new Error('DB não configurado (DATABASE_URL ausente)');
    return r.json();
  }).then(rows => {
    const tb = document.getElementById('systems-body');
    tb.innerHTML = '';
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="8" style="text-align: center; padding: 2rem; color: var(--text); opacity: 0.7;">Nenhum sistema registrado</td></tr>';
      btn.textContent = originalText;
      btn.disabled = false;
      return;
    }
    for (const s of rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(s.system_key)}</td>
        <td style="font-weight: 500;">${escapeHtml(s.name)}</td>
        <td>${escapeHtml(s.vendor)}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(s.connector_type)}</td>
        <td><span class="pill">${escapeHtml(s.environment)}</span></td>
        <td>${pill(s.status)}</td>
        <td style="font-family: monospace; font-size: 0.85rem; opacity: 0.8;">${escapeHtml(s.base_url || '—')}</td>
        <td>
          <a href="/admin/incidents?system_key=${encodeURIComponent(s.system_key)}" class="btn" style="padding: 0.4rem 0.8rem; background: var(--border);">📋</a>
          <button data-click="toggleStatus" data-args="${escapeAttr(JSON.stringify([s.id, s.status === 'active' ? 'degraded' : 'active']))}" class="btn" style="padding: 0.4rem 0.8rem; background: var(--border);">🔄</button>
          <button data-click="delSystem" data-args="${escapeAttr(JSON.stringify([s.id]))}" class="btn btn-danger" style="padding: 0.4rem 0.8rem;">🗑️</button>
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

function toggleStatus(id, target) {
  if (!confirm(target === 'active' ? 'Marcar este sistema como ativo?' : 'Marcar este sistema como degradado?')) return;
  adminFetch('/admin/api/systems/' + id, {method:'PATCH', body: JSON.stringify({status: target})})
    .then(() => loadSystems()).catch(e => document.getElementById('msg').textContent = e.message);
}

function delSystem(id) {
  if (!confirm('Remover este sistema permanentemente?')) return;
  adminFetch('/admin/api/systems/' + id, {method:'DELETE'})
    .then(() => loadSystems()).catch(e => document.getElementById('msg').textContent = e.message);
}

function addSystem(e) {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = Object.fromEntries([...f.entries()].map(([k,v]) => [k, v === '' ? null : v]));

  const btn = e.target.querySelector('button[type="submit"]');
  const originalText = btn.textContent;
  btn.textContent = 'Adicionando...';
  btn.disabled = true;

  adminFetch('/admin/api/systems', {method:'POST', body: JSON.stringify(body)})
    .then(r => r.ok ? loadSystems() : r.json().then(j => { throw new Error(j.detail); }))
    .then(() => e.target.reset())
    .catch(err => document.getElementById('msg').textContent = err.message)
    .finally(() => { btn.textContent = originalText; btn.disabled = false; });

  return false;
}

loadSystems();
