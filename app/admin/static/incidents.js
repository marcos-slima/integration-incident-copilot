// incidents (Admin UI) - extraido do template para respeitar a CSP (script-src 'self').
function systemCell(sys) {
  if (!sys || sys.match === 'none') return `<span class="status-badge status-offline">${escapeHtml(t('incidents.noMatch'))}</span>`;
  if (sys.match === 'ambiguous') {
    return `<span class="status-badge status-degraded">${escapeHtml(t('incidents.ambiguous'))}</span> <code style="font-size: 0.85rem; margin-left: 0.5rem;">${escapeHtml((sys.candidates || []).join(', '))}</code>`;
  }
  return `${pill(sys.status)} <span style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(sys.system_key)}</span> <span style="opacity: 0.7;">(${escapeHtml(sys.match)})</span>`;
}

function verifiedCell(i) {
  if (i.verified_at == null) return `<span class="status-badge status-degraded">⏳ ${escapeHtml(t('incidents.pending'))}</span>`;
  if (i.diagnosis_correct === true) return `<span class="status-badge status-active">✅ ${escapeHtml(t('incidents.correct'))}</span>`;
  if (i.diagnosis_correct === false) return `<span class="status-badge status-offline">❌ ${escapeHtml(t('incidents.incorrect'))}</span>`;
  return `<span class="status-badge" style="background: var(--border); color: var(--text);">${escapeHtml(t('incidents.verified'))}</span>`;
}

// DA-44: sinal de escalonamento gravado desde a migration 019; o motivo e'
// um codigo estavel, traduzido pelo catalogo (DA-73).
function escalationReason(code) {
  if (!code) return '';
  return typeof _i18nLookup('escalation.' + code) === 'string' ? t('escalation.' + code) : code;
}

function escalationCell(i) {
  if (i.should_escalate == null) return '<span style="opacity: 0.6;">—</span>';
  const title = escapeAttr(escalationReason(i.escalation_reason));
  if (i.should_escalate) return `<span class="status-badge status-degraded" title="${title}">⚠ ${escapeHtml(t('incidents.review'))}</span>`;
  return `<span class="status-badge status-active" title="${title}">${escapeHtml(t('incidents.ok'))}</span>`;
}

function costText(i) {
  if (i.cost_usd == null) return '—';
  if (i.llm_calls === 0) return t('incidents.costNoLlm', {cost: fmtUSD(0)});
  return t('incidents.cost', {cost: fmtUSD(Number(i.cost_usd), 4), count: i.llm_calls});
}

// Evidence gravada = app/models.py::Evidence (source_type, source_id, locator,
// excerpt, trust_level). Antes lia e.source/e.description, que nao existem,
// e caia no JSON.stringify da linha inteira.
function evidenceLine(e) {
  const where = `${e.source_type || '?'}:${e.source_id || '?'}${e.locator ? ' · ' + e.locator : ''}`;
  const excerpt = (e.excerpt || '').replace(/\s+/g, ' ').slice(0, 240);
  return `- [${e.trust_level || '?'}] ${where}${excerpt ? '\n  ' + excerpt : ''}`;
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
    if (r.status === 401) throw new Error(t('common.invalidKey'));
    if (r.status === 503) throw new Error(t('common.dbMissing'));
    return r.json();
  }).then(rows => {
    const tb = document.getElementById('incidents-body');
    tb.innerHTML = '';
    const count = document.getElementById('count');

    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="13" style="text-align: center; padding: 2rem; color: var(--text); opacity: 0.7;">' + escapeHtml(t('incidents.empty')) + '</td></tr>';
      count.textContent = t('incidents.count', {count: 0});
      return;
    }
    count.textContent = t('incidents.count', {count: rows.length});

    for (const i of rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(fmtDateTime(i.created_at))}</td>
        <td><span style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(String(i.id).slice(0, 8))}</span></td>
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(i.interface_type || '—')}</td>
        <td>${escapeHtml(i.connector_source_system || '—')}</td>
        <td>${systemCell(i.system)}</td>
        <td style="max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(i.probable_root_cause || '')}">${escapeHtml((i.probable_root_cause || '—').slice(0, 60))}</td>
        <td>${i.diagnosis_confidence == null ? '—' : escapeHtml(fmtNumber(i.diagnosis_confidence))}</td>
        <td>${i.evidence_strength == null ? '—' : escapeHtml(fmtNumber(i.evidence_strength))}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(i.llm_provider_used || '—')}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(i.latency_ms == null ? '—' : fmtNumber(i.latency_ms, 0) + ' ms')}</td>
        <td>${escalationCell(i)}</td>
        <td>${verifiedCell(i)}</td>
        <td><button data-click="loadDetail" data-args="${escapeAttr(JSON.stringify([i.id]))}" class="btn btn-primary" style="padding: 0.4rem 0.8rem;" title="${escapeAttr(t('incidents.detailTitle'))}">📋</button></td>`;
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
    if (r.status === 404) throw new Error(t('incidents.notFound'));
    return r.json();
  }).then(i => {
    const el = document.getElementById('detail');
    const ev = (i.evidence || []).map(evidenceLine).join('\n');
    const prov = i.llm_provider_used === 'rule_engine'
      ? t('incidents.ruleEngine')
      : [i.llm_provider_used || '?', i.llm_model, i.prompt_version && t('incidents.prompt', {version: i.prompt_version})].filter(Boolean).join(' · ');
    el.innerHTML = `
      <div style="display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem;">
        <span style="font-size: 1.2rem; font-weight: 700;">${escapeHtml(i.id)}</span>
        ${systemCell(i.system)}
      </div>

      <div style="background: var(--border); padding: 1rem; border-radius: 8px; margin-bottom: 1rem;">
        <strong style="color: var(--text); margin-bottom: 0.5rem; display: block;">${escapeHtml(t('incidents.dDescription'))}</strong>
        <pre style="margin: 0; white-space: pre-wrap;">${escapeHtml(i.description || t('incidents.noDescription'))}</pre>
      </div>

      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
        <div class="card" style="padding: 1rem;">
          <strong style="color: var(--text); margin-bottom: 0.5rem; display: block;">${escapeHtml(t('incidents.dRootCause'))}</strong>
          <p style="margin: 0; color: var(--text);">${escapeHtml(i.probable_root_cause || '—')}</p>
        </div>
        <div class="card" style="padding: 1rem;">
          <strong style="color: var(--text); margin-bottom: 0.5rem; display: block;">${escapeHtml(t('incidents.dConfirmed'))}</strong>
          <p style="margin: 0; color: var(--text);">${escapeHtml(i.verified_root_cause || '—')}
            <span style="opacity: 0.7;">${escapeHtml(t('incidents.by', {who: i.verified_by || '—'}))}</span></p>
        </div>
      </div>

      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1rem;">
        <div class="card" style="padding: 1rem;">
          <strong style="color: var(--text); margin-bottom: 0.5rem; display: block;">${escapeHtml(t('incidents.dProvenance'))}</strong>
          <p style="margin: 0; color: var(--text);">${escapeHtml(prov)}${i.agent_domain ? ' · ' + escapeHtml(t('incidents.domain', {domain: i.agent_domain})) : ''}</p>
          <p style="margin: 0.25rem 0 0; font-family: monospace; font-size: 0.8rem; opacity: 0.7;" title="prompt_digest">${escapeHtml(i.prompt_digest || '')}</p>
        </div>
        <div class="card" style="padding: 1rem;">
          <strong style="color: var(--text); margin-bottom: 0.5rem; display: block;">${escapeHtml(t('incidents.dEscalationCost'))}</strong>
          <p style="margin: 0; color: var(--text);">${escalationCell(i)} ${escapeHtml(escalationReason(i.escalation_reason))}</p>
          <p style="margin: 0.25rem 0 0; color: var(--text);">${escapeHtml(costText(i))}</p>
        </div>
      </div>

      <div class="card" style="padding: 1rem; margin-top: 1rem;">
        <strong style="color: var(--text); margin-bottom: 0.5rem; display: block;">${escapeHtml(t('incidents.dErrorCodes'))}</strong>
        <p style="margin: 0; color: var(--text); font-family: monospace;">${escapeHtml((i.error_codes || []).join(', ') || '—')}</p>
      </div>

      <div class="card" style="padding: 1rem; margin-top: 1rem;">
        <strong style="color: var(--text); margin-bottom: 0.5rem; display: block;">${escapeHtml(t('incidents.dEvidence', {count: (i.evidence || []).length}))}</strong>
        <pre style="margin: 0; white-space: pre-wrap;">${escapeHtml(ev || t('incidents.none'))}</pre>
      </div>
    `;
  }).catch(e => {
    const el = document.getElementById('detail');
    el.innerHTML = `<p style="color: var(--error);">${escapeHtml(t('common.error', {message: e.message}))}</p>`;
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
