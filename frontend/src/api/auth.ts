// Auth de sessão da UI (DA-54) — POST /auth/login troca usuário+senha por
// cookie HttpOnly, e o browser o envia sozinho em toda chamada. Por isso
// este arquivo não toca em `api/diagnose.ts`: cookies fluem no fetch
// same-origin sem nenhuma mudança lá — exatamente o que o comentário
// daquele arquivo previa ("essa evolução não exige mudança neste arquivo").
// X-API-Key permanece como credencial de máquina (curl/MCP/A2A), na camada
// dela — não mais na mão do usuário da UI.

import { ApiError } from './diagnose';

export interface SessionState {
  authenticated: boolean;
  username?: string;
  ttl_hours?: number;
}

export async function getSession(): Promise<SessionState> {
  const res = await fetch('/auth/session', { headers: { Accept: 'application/json' } });
  if (!res.ok) throw new ApiError(res.status, 'Falha ao consultar a sessão');
  return res.json();
}

export async function login(username: string, password: string): Promise<SessionState> {
  const res = await fetch('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const message =
      res.status === 429
        ? 'Muitas tentativas — aguarde um minuto e tente de novo'
        : 'usuário ou senha inválidos';
    throw new ApiError(res.status, message);
  }
  return res.json();
}

export async function logout(): Promise<void> {
  await fetch('/auth/logout', { method: 'POST' });
}

// DA-55: ativacao de conta em duas etapas (rotas publicas, rate-limited).
// Respostas 401 sao GENERICAS de proposito: usuario inexistente ==
// token/codigo errado — o servidor nao revela quem existe.
export async function verifyEmail(username: string, token: string) {
  const res = await fetch('/auth/verify/email', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, token }),
  });
  if (!res.ok) {
    const message =
      res.status === 429
        ? 'Muitas tentativas — aguarde um minuto'
        : 'token inválido ou usuário inexistente';
    throw new ApiError(res.status, message);
  }
  return res.json();
}

export async function verifyPhone(username: string, code: string) {
  const res = await fetch('/auth/verify/phone', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, code }),
  });
  if (!res.ok) {
    const message =
      res.status === 429
        ? 'Muitas tentativas — aguarde um minuto'
        : 'código inválido ou usuário inexistente';
    throw new ApiError(res.status, message);
  }
  return res.json();
}
