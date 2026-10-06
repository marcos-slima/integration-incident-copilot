// users (Admin UI) - extraido do template para respeitar a CSP (script-src 'self').
function userPill(status) {
  const cls = status === 'active' ? 'status-active' : (status === 'disabled' ? 'status-offline' : 'status-degraded');
  return `<span class="status-badge ${cls}">${escapeHtml(status)}</span>`;
}

function showHandoff(text) {
  const el = document.getElementById('handoff');
  const container = document.getElementById('handoff-container');
  el.textContent = text;
  container.style.display = 'block';
}

function loadUsers() {
  const btn = actionButton('loadUsers');
  const originalText = btn.textContent;
  btn.textContent = 'Carregando...';
  btn.disabled = true;

  adminFetch('/admin/api/users').then(r => {
    if (r.status === 401) throw new Error('Chave admin inválida — insira acima');
    if (r.status === 503) throw new Error('DB não configurado (DATABASE_URL ausente)');
    return r.json();
  }).then(rows => {
    const tb = document.getElementById('users-body');
    tb.innerHTML = '';
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="8" style="text-align: center; padding: 2rem; color: var(--text); opacity: 0.7;">Nenhum usuário</td></tr>';
      btn.textContent = originalText;
      btn.disabled = false;
      return;
    }
    for (const u of rows) {
      const tr = document.createElement('tr');
      const actions = [];
      if (u.status === 'pending_email')
        actions.push(`<button data-click="reissue" data-args="${escapeAttr(JSON.stringify([u.id, 'email-token']))}" class="btn" style="padding: 0.4rem 0.8rem; background: var(--border);">✉️</button>`);
      if (u.status === 'pending_phone')
        actions.push(`<button data-click="reissue" data-args="${escapeAttr(JSON.stringify([u.id, 'phone-code']))}" class="btn" style="padding: 0.4rem 0.8rem; background: var(--border);">📱</button>`);
      if (u.status === 'active')
        actions.push(`<button data-click="setStatus" data-args="${escapeAttr(JSON.stringify([u.id, 'disabled']))}" class="btn btn-danger" style="padding: 0.4rem 0.8rem;">🚫</button>`);
      if (u.status === 'disabled')
        actions.push(`<button data-click="setStatus" data-args="${escapeAttr(JSON.stringify([u.id, 'active']))}" class="btn btn-success" style="padding: 0.4rem 0.8rem;">✅</button>`);
      actions.push(`<button data-click="delUser" data-args="${escapeAttr(JSON.stringify([u.id]))}" class="btn btn-danger" style="padding: 0.4rem 0.8rem;">🗑️</button>`);
      tr.innerHTML = `
        <td style="font-weight: 500;">${escapeHtml(u.username)}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(u.email)}</td>
        <td style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(u.phone)}</td>
        <td>${userPill(u.status)}</td>
        <td>${u.email_verified_at ? '✓' : '—'}</td>
        <td>${u.phone_verified_at ? '✓' : '—'}</td>
        <td style="font-family: monospace; font-size: 0.9rem; opacity: 0.7;">${escapeHtml(u.created_by)}</td>
        <td>${actions.join(' ')}</td>`;
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

function addUser(ev) {
  ev.preventDefault();
  const f = ev.target;
  const body = {
    username: f.username.value.trim(),
    email: f.email.value.trim(),
    phone: f.phone.value.trim(),
    password: f.password.value,
  };
  adminFetch('/admin/api/users', { method: 'POST', body: JSON.stringify(body) })
    .then(async r => {
      if (r.status === 401) throw new Error('Chave admin inválida');
      const data = await r.json();
      if (r.status === 409) throw new Error(data.detail || 'Usuário já existe');
      if (!r.ok) throw new Error(JSON.stringify(data));
      if (data.activation && data.activation.email_token) {
        showHandoff(
          'OUT-OF-BAND — entregue ao usuário pelo canal que você controlar:\n' +
          'usuário: ' + escapeHtml(data.username) + '\n' +
          '1) token de e-mail (vale 24h): ' + data.activation.email_token +
          '\n2) após confirmar o token, reemita o código de telefone aqui.');
      } else {
        document.getElementById('msg').textContent =
          'Criado — token enviado por e-mail de verdade';
      }
      f.reset();
      document.getElementById('handoff-container').style.display = 'none';
      loadUsers();
    })
    .catch(e => {
      const msg = document.getElementById('msg');
      msg.textContent = e.message;
      msg.style.display = 'block';
    });
  return false;
}

function reissue(id, kind) {
  adminFetch(`/admin/api/users/${id}/${kind}`, { method: 'POST' })
    .then(async r => {
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.status);
      const a = data.activation || {};
      if (a.email_token) showHandoff('Token de e-mail (vale 24h): ' + a.email_token);
      else if (a.phone_code) showHandoff('Código de telefone (vale 10 min): ' + a.phone_code);
      else document.getElementById('msg').textContent = 'Enviado de verdade';
      loadUsers();
    })
    .catch(e => {
      const msg = document.getElementById('msg');
      msg.textContent = e.message;
      msg.style.display = 'block';
    });
}

function setStatus(id, status) {
  const btn = this instanceof HTMLElement ? this : document.createElement('button');
  const originalText = btn.textContent;
  btn.textContent = '...';
  btn.disabled = true;

  adminFetch(`/admin/api/users/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) })
    .then(r => {
      if (!r.ok) throw new Error(r.status);
      loadUsers();
    })
    .catch(e => {
      const msg = document.getElementById('msg');
      msg.textContent = e.message;
      msg.style.display = 'block';
    })
    .finally(() => {
      btn.textContent = originalText;
      btn.disabled = false;
    });
}

function delUser(id) {
  const btn = this instanceof HTMLElement ? this : document.createElement('button');
  const originalText = btn.textContent;
  btn.textContent = '...';
  btn.disabled = true;

  adminFetch(`/admin/api/users/${id}`, { method: 'DELETE' })
    .then(r => { if (!r.ok && r.status !== 204) throw new Error(r.status); loadUsers(); })
    .catch(e => {
      const msg = document.getElementById('msg');
      msg.textContent = e.message;
      msg.style.display = 'block';
    })
    .finally(() => {
      btn.textContent = originalText;
      btn.disabled = false;
    });
}

loadUsers();
