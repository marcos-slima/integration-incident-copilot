// Auth de sessão da UI (DA-54) — POST /auth/login troca usuário+senha por
// cookie HttpOnly, e o browser o envia sozinho em toda chamada. Por isso
// este arquivo não toca em `api/diagnose.ts`: cookies fluem no fetch
// same-origin sem nenhuma mudança lá — exatamente o que o comentário
// daquele arquivo previa ("essa evolução não exige mudança neste arquivo").
// X-API-Key permanece como credencial de máquina (curl/MCP/A2A), na camada
// dela — não mais na mão do usuário da UI.

import i18n from 'i18next';
import { ApiError, apiBase } from './http';

export interface SessionState {
  authenticated: boolean;
  username?: string;
  ttl_hours?: number;
}

// DA-73: mensagem pelo `code` estavel do backend; sem code, pelo status.
async function authErrorMessage(res: Response, invalidKey: string, tooManyKey: string): Promise<string> {
  const body = await res.json().catch(() => ({}));
  switch (body.code) {
    case 'too_many_attempts':
      return i18n.t(tooManyKey);
    case 'activation_requires_db':
      return i18n.t('core.auth.activationRequiresDb');
    case 'invalid_credentials':
    case 'invalid_token':
    case 'invalid_code':
      return i18n.t(invalidKey);
  }
  return res.status === 429 ? i18n.t(tooManyKey) : i18n.t(invalidKey);
}

export async function getSession(): Promise<SessionState> {
  const res = await fetch(`${apiBase()}/auth/session`, { headers: { Accept: 'application/json' } });
  if (!res.ok) throw new ApiError(res.status, i18n.t('core.auth.sessionCheckFailed'));
  return res.json();
}

export async function login(username: string, password: string): Promise<SessionState> {
  const res = await fetch(`${apiBase()}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    throw new ApiError(res.status, await authErrorMessage(res, 'core.auth.invalidCredentials', 'core.auth.tooManyLogin'));
  }
  return res.json();
}

export async function logout(): Promise<void> {
  await fetch(`${apiBase()}/auth/logout`, { method: 'POST' });
}

// DA-55: ativacao de conta em duas etapas (rotas publicas, rate-limited).
// Respostas 401 sao GENERICAS de proposito: usuario inexistente ==
// token/codigo errado — o servidor nao revela quem existe.
export async function verifyEmail(username: string, token: string) {
  const res = await fetch(`${apiBase()}/auth/verify/email`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, token }),
  });
  if (!res.ok) {
    throw new ApiError(res.status, await authErrorMessage(res, 'core.auth.invalidToken', 'core.auth.tooMany'));
  }
  return res.json();
}

export async function verifyPhone(username: string, code: string) {
  const res = await fetch(`${apiBase()}/auth/verify/phone`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, code }),
  });
  if (!res.ok) {
    throw new ApiError(res.status, await authErrorMessage(res, 'core.auth.invalidCode', 'core.auth.tooMany'));
  }
  return res.json();
}
