// usage (Admin UI) - extraido do template para respeitar a CSP (script-src 'self').
function loadUsage() {
  const btn = actionButton('loadUsage');
  const originalText = btn.textContent;
  btn.textContent = t('common.loading');
  btn.disabled = true;

  adminFetch('/admin/api/usage').then(r => {
    if (r.status === 401) throw new Error(t('common.invalidKey'));
    if (r.status === 503) throw new Error(t('common.dbMissing'));
    return r.json();
  }).then(rows => {
    const tb = document.getElementById('usage-body');
    tb.innerHTML = '';
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="11" style="text-align: center; padding: 2rem; color: var(--text); opacity: 0.7;">' + escapeHtml(t('usage.empty')) + '</td></tr>';
      btn.textContent = originalText;
      btn.disabled = false;
      return;
    }
    for (const u of rows) {
      const tr = document.createElement('tr');
      const pctClass = u.percent != null && u.percent >= 80 ? 'status-degraded' : 'status-active';
      tr.innerHTML = `
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(u.provider_origin)}</td>
        <td style="font-weight: 500;"><span style="font-size: 0.85rem; opacity: 0.6; margin-right: 0.25rem;">🤖</span>${escapeHtml(u.model_id)}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${fmtNumber(u.tokens_in, 0)}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${fmtNumber(u.tokens_out, 0)}</td>
        <td style="font-weight: 600; font-family: monospace; font-size: 0.9rem;">${fmtNumber(u.tokens_total, 0)}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${fmtNumber(u.requests, 0)}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${fmtNumber(u.failures, 0)}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${fmtUSD(u.cost_usd, 4)}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${u.monthly_limit_tokens == null ? '—' : fmtNumber(u.monthly_limit_tokens, 0)}</td>
        <td>${u.percent == null ? '—' : `<span class="status-badge ${pctClass}">${fmtNumber(u.percent, 1)}%</span>`}</td>
        <td><button data-click="resetUsage" data-args="${escapeAttr(JSON.stringify([u.provider_origin, u.model_id]))}" class="btn btn-danger" style="padding: 0.4rem 0.8rem;" title="${escapeAttr(t('usage.resetTitle'))}">🗑️</button></td>`;
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

function resetUsage(origin, model) {
  if (!confirm(t('usage.confirmReset', {model: model}))) return;
  adminFetch('/admin/api/usage/' + encodeURIComponent(origin) + '/' + encodeURIComponent(model) + '/reset',
    {method:'POST'}).then(() => loadUsage()).catch(e => {
      const msg = document.getElementById('msg');
      msg.textContent = e.message;
      msg.style.display = 'block';
    });
}

loadUsage();
