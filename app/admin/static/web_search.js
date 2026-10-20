// web_search (Admin UI) - extraido do template para respeitar a CSP (script-src 'self').
function loadSources() {
  const btn = actionButton('loadSources');
  const originalText = btn.textContent;
  btn.textContent = t('common.loading');
  btn.disabled = true;

  adminFetch('/admin/api/web-search-sources').then(r => {
    if (r.status === 401) throw new Error(t('common.invalidKey'));
    if (r.status === 503) throw new Error(t('common.dbMissing'));
    return r.json();
  }).then(rows => {
    const tb = document.getElementById('sources-body');
    tb.innerHTML = '';
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 2rem; color: var(--text); opacity: 0.7;">' + t('web.empty') + '</td></tr>';
      btn.textContent = originalText;
      btn.disabled = false;
      return;
    }
    for (const s of rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(s.interface_type)}</td>
        <td style="font-family: sans-serif;">${escapeHtml(s.tech_term)}</td>
        <td style="font-family: monospace; font-size: 0.85rem;">${escapeHtml(s.site_filter)}</td>
        <td>${s.enabled ? `<span class="status-badge status-active">${escapeHtml(t('common.yes'))}</span>` : `<span class="status-badge status-offline">${escapeHtml(t('common.no'))}</span>`}</td>
        <td>${escapeHtml(s.notes || '—')}</td>
        <td>
          <button data-click="editSource" data-args="${escapeAttr(JSON.stringify([s.id]))}" class="btn" style="padding: 0.4rem 0.8rem; background: var(--border);" title="${escapeAttr(t('common.edit'))}">✎</button>
          <button data-click="toggleSource" data-args="${escapeAttr(JSON.stringify([s.id, !s.enabled]))}" class="btn" style="padding: 0.4rem 0.8rem; background: var(--border);" title="${escapeAttr(t('common.toggle'))}">🔄</button>
          <button data-click="delSource" data-args="${escapeAttr(JSON.stringify([s.id]))}" class="btn btn-danger" style="padding: 0.4rem 0.8rem;" title="${escapeAttr(t('common.delete'))}">🗑️</button>
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

function editSource(id) {
  const f = document.getElementById('edit-form');
  f.dataset.id = id;
  f.querySelector('[name=tech_term]').value = '';
  f.querySelector('[name=site_filter]').value = '';
  f.querySelector('[name=notes]').value = '';
  document.getElementById('edit-id').textContent = id;
  document.getElementById('edit-form').hidden = false;
}

function cancelEdit() {
  document.getElementById('edit-form').hidden = true;
}

function saveEdit(e) {
  e.preventDefault();
  const f = e.target;
  const fd = new FormData(f);
  const body = {};
  for (const [k, v] of fd.entries()) if (v !== '') body[k] = v;
  if (!Object.keys(body).length) { cancelEdit(); return false; }
  adminFetch('/admin/api/web-search-sources/' + f.dataset.id,
    {method:'PATCH', body: JSON.stringify(body)})
    .then(r => r.ok ? null : r.json().then(j => { throw new Error(apiErrorText(j, r.status)); }))
    .then(() => { cancelEdit(); return loadSources(); })
    .catch(err => {
      const msg = document.getElementById('msg');
      msg.textContent = err.message;
      msg.style.display = 'block';
    });
  return false;
}

function toggleSource(id, enabled) {
  adminFetch('/admin/api/web-search-sources/' + id, {method:'PATCH', body: JSON.stringify({enabled})})
    .then(() => loadSources()).catch(e => {
      const msg = document.getElementById('msg');
      msg.textContent = e.message;
      msg.style.display = 'block';
    });
}

function delSource(id) {
  if (!confirm(t('web.confirmDelete'))) return;
  adminFetch('/admin/api/web-search-sources/' + id, {method:'DELETE'})
    .then(() => loadSources()).catch(e => {
      const msg = document.getElementById('msg');
      msg.textContent = e.message;
      msg.style.display = 'block';
    });
}

function addSource(e) {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = Object.fromEntries([...f.entries()].map(([k,v]) => [k, v === '' ? null : v]));
  body.enabled = true;
  adminFetch('/admin/api/web-search-sources', {method:'POST', body: JSON.stringify(body)})
    .then(r => r.ok ? loadSources() : r.json().then(j => { throw new Error(apiErrorText(j, r.status)); }))
    .then(() => e.target.reset())
    .catch(err => {
      const msg = document.getElementById('msg');
      msg.textContent = err.message;
      msg.style.display = 'block';
    });
  return false;
}

loadSources();
