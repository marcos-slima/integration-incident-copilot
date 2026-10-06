// incidents (Admin UI) - extraido do template para respeitar a CSP (script-src 'self').
function systemCell(sys) {
  if (!sys || sys.match === 'none') return '<span class="status-badge status-offline">Sem correspondência</span>';
  if (sys.match === 'ambiguous') {
    return `<span class="status-badge status-degraded">Ambíguo</span> <code style="font-size: 0.85rem; margin-left: 0.5rem;">${escapeHtml((sys.candidates || []).join(', '))}</code>`;
  }
  const label = sys.system_key + ' · ' + (sys.name || '-');
  return `${pill(sys.status)} <span style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(sys.system_key)}</span> <span style="opacity: 0.7;">(${escapeHtml(sys.match)})</span>`;
}

function verifiedCell(i) {
  if (i.verified_at == null) return '<span class="status-badge status-degraded">⏳ Pendente</span>';
  if (i.diagnosis_correct === true) return '<span class="status-badge status-active">✅ Correto</span>';
  if (i.diagnosis_correct === false) return '<span class="status-badge status-offline">❌ Incorreto</span>';
  return '<span class="status-badge" style="background: var(--border); color: var(--text);">Verificado</span>';
}

function loadIncidents() {
  const params = new URLSearchParams();
  const sys = document.getElementById('f-system').value.trim();
  const itf = document.getElementById('f-interface').value.trim();
  const ver = document.getElementById('f-verified').value;
  if (sys) params.set('system_key', sys);
  if (itf) params.set('interface_type', itf);
  if (ver) params.set('verified', ver);

  const url = '/admin/api/incidents?' + params.toString();
  adminFetch(url).then(r => {
    if (r.status === 401) throw new Error('Chave admin inválida — insira acima');
    if (r.status === 503) throw new Error('DB não configurado (DATABASE_URL ausente)');
    return r.json();
  }).then(rows => {
    const tb = document.getElementById('incidents-body');
    tb.innerHTML = '';
    const count = document.getElementById('count');

    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="12" style="text-align: center; padding: 2rem; color: var(--text); opacity: 0.7;">Nenhum incidente para o filtro atual</td></tr>';
      count.textContent = '0 incidentes';
      return;
    }
    count.textContent = `${rows.length} incidentes`;

    for (const i of rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml((i.created_at || '').replace('T', ' ').slice(0, 19))}</td>
        <td><span style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(String(i.id).slice(0, 8))}</span></td>
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(i.interface_type || '—')}</td>
        <td>${escapeHtml(i.connector_source_system || '—')}</td>
        <td>${systemCell(i.system)}</td>
        <td style="max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(i.probable_root_cause || '')}">${escapeHtml((i.probable_root_cause || '—').slice(0, 60))}</td>
        <td>${i.diagnosis_confidence == null ? '—' : escapeHtml(i.diagnosis_confidence)}</td>
        <td>${i.evidence_strength == null ? '—' : escapeHtml(i.evidence_strength)}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(i.llm_provider_used || '—')}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(i.latency_ms == null ? '—' : i.latency_ms + ' ms')}</td>
        <td>${verifiedCell(i)}</td>
        <td><button data-click="loadDetail" data-args="${escapeAttr(JSON.stringify([i.id]))}" class="btn btn-primary" style="padding: 0.4rem 0.8rem;">📋</button></td>`;
      tb.appendChild(tr);
    }
  }).catch(e => {
    const msg = document.getElementById('msg');
    msg.textContent = e.message;
    msg.style.display = 'block';
  });
}

function loadDetail(id) {
  adminFetch('/admin/api/incidents/' + encodeURIComponent(id)).then(r => {
    if (r.status === 404) throw new Error('Incidente não encontrado');
    return r.json();
  }).then(i => {
    const el = document.getElementById('detail');
    const ev = (i.evidence || []).map(e => `- [${e.trust_level || '?'}] ${e.source || e.description || JSON.stringify(e)}`).join('\n');
    el.innerHTML = `
      <div style="display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem;">
        <span style="font-size: 1.2rem; font-weight: 700;">${escapeHtml(i.id)}</span>
        ${systemCell(i.system)}
      </div>

      <div style="background: var(--border); padding: 1rem; border-radius: 8px; margin-bottom: 1rem;">
        <strong style="color: var(--text); margin-bottom: 0.5rem; display: block;">Descrição:</strong>
        <pre style="margin: 0; white-space: pre-wrap;">${escapeHtml(i.description || '(sem descrição)')}</pre>
      </div>

      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
        <div class="card" style="padding: 1rem;">
          <strong style="color: var(--text); margin-bottom: 0.5rem; display: block;">Causa raiz provável:</strong>
          <p style="margin: 0; color: var(--text);">${escapeHtml(i.probable_root_cause || '—')}</p>
        </div>
        <div class="card" style="padding: 1rem;">
          <strong style="color: var(--text); margin-bottom: 0.5rem; display: block;">Causa confirmada:</strong>
          <p style="margin: 0; color: var(--text);">${escapeHtml(i.verified_root_cause || '—')}
            <span style="opacity: 0.7;">(por ${escapeHtml(i.verified_by || '—')})</span></p>
        </div>
      </div>

      <div class="card" style="padding: 1rem; margin-top: 1rem;">
        <strong style="color: var(--text); margin-bottom: 0.5rem; display: block;">Códigos de erro:</strong>
        <p style="margin: 0; color: var(--text); font-family: monospace;">${escapeHtml((i.error_codes || []).join(', ') || '—')}</p>
      </div>

      <div class="card" style="padding: 1rem; margin-top: 1rem;">
        <strong style="color: var(--text); margin-bottom: 0.5rem; display: block;">Evidências (${(i.evidence || []).length}):</strong>
        <pre style="margin: 0;">${escapeHtml(ev || '(nenhuma)')}</pre>
      </div>
    `;
  }).catch(e => {
    const el = document.getElementById('detail');
    el.innerHTML = `<p style="color: var(--error);">Erro: ${escapeHtml(e.message)}</p>`;
  });
}

function clearFilters() {
  document.getElementById('f-system').value = '';
  document.getElementById('f-interface').value = '';
  document.getElementById('f-verified').value = '';
  history.replaceState(null, '', '/admin/incidents');
  loadIncidents();
}

(function initFromUrl() {
  const sys = new URLSearchParams(location.search).get('system_key');
  if (sys) document.getElementById('f-system').value = sys;
  loadIncidents();
})();
