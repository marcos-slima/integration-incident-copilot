// usage (Admin UI) - extraido do template para respeitar a CSP (script-src 'self').
function loadUsage() {
  const btn = actionButton('loadUsage');
  const originalText = btn.textContent;
  btn.textContent = 'Carregando...';
  btn.disabled = true;

  adminFetch('/admin/api/usage').then(r => {
    if (r.status === 401) throw new Error('Chave admin inválida — insira acima');
    if (r.status === 503) throw new Error('DB não configurado (DATABASE_URL ausente)');
    return r.json();
  }).then(rows => {
    const tb = document.getElementById('usage-body');
    tb.innerHTML = '';
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="11" style="text-align: center; padding: 2rem; color: var(--text); opacity: 0.7;">Sem modelos registrados</td></tr>';
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
        <td style="font-family: monospace; font-size: 0.9rem;">${u.tokens_in}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${u.tokens_out}</td>
        <td style="font-weight: 600; font-family: monospace; font-size: 0.9rem;">${u.tokens_total}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${u.requests}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${u.failures}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${u.cost_usd}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${u.monthly_limit_tokens ?? '—'}</td>
        <td>${u.percent == null ? '—' : `<span class="status-badge ${pctClass}">${u.percent}%</span>`}</td>
        <td><button data-click="resetUsage" data-args="${escapeAttr(JSON.stringify([u.provider_origin, u.model_id]))}" class="btn btn-danger" style="padding: 0.4rem 0.8rem;">🗑️</button></td>`;
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
  if (!confirm('Fechar o período atual e zerar o contador de ' + escapeHtml(model) + '?')) return;
  adminFetch('/admin/api/usage/' + encodeURIComponent(origin) + '/' + encodeURIComponent(model) + '/reset',
    {method:'POST'}).then(() => loadUsage()).catch(e => {
      const msg = document.getElementById('msg');
      msg.textContent = e.message;
      msg.style.display = 'block';
    });
}

loadUsage();
