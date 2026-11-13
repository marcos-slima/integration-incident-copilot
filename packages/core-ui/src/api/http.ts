// Cliente HTTP do core (DA-95). Unico lugar que conhece a base da API, o
// formato de erro do FastAPI e a politica de credencial da UI (cookie de
// sessao HttpOnly, DA-54). Shells de produto nunca chamam fetch() direto.

import i18n from 'i18next';
import type { ApiErrorCode } from './contracts';

export const HTTP_CONTRACT_VERSION = '1.0';

let base = '';

/** Base da API (vazio = mesma origem; no dev, o proxy do Vite). */
export function configureApi(options: { baseUrl?: string }): void {
  base = (options.baseUrl ?? '').replace(/\/$/, '');
}

export function apiBase(): string {
  return base;
}

export class ApiError extends Error {
  status: number;
  code?: ApiErrorCode | string;
  errorId?: string; // liga a resposta ao log do servidor
  constructor(status: number, message: string, code?: string, errorId?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.errorId = errorId;
  }
}

// Corpo de erro do FastAPI: `detail` e string nos handlers proprios e lista
// no 422 de validacao (M-15). Nunca devolve o valor recebido.
export async function apiErrorFrom(res: Response): Promise<ApiError> {
  const err = await res.json().catch(() => ({}));
  const detail = Array.isArray(err.detail)
    ? err.detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join('; ')
    : err.detail;
  return new ApiError(res.status, detail || `HTTP ${res.status}`, err.code, err.error_id);
}

/** Mensagem para o usuario, por `code` quando existe e por status no resto (DA-73). */
export function describeApiError(e: ApiError): string {
  const ref = e.errorId ? i18n.t('core.errors.ref', { id: e.errorId }) : '';
  switch (e.code) {
    case 'incompatible_contract':
    case 'budget_exhausted':
    case 'policy_violation':
    case 'domain_context_invalid':
      return i18n.t(`core.errors.${e.code}`) + ref;
    case 'overloaded':
      return i18n.t('core.errors.overloaded');
    case 'timeout':
      return i18n.t('core.errors.timeout', { detail: e.message });
  }
  if (e.status === 422) return i18n.t('core.errors.validation', { message: e.message });
  if (e.status === 429) return i18n.t('core.errors.rateLimited');
  return i18n.t('core.errors.generic', { status: e.status, message: e.message }) + ref;
}

/** GET/POST JSON na API do core com a credencial de sessao. */
export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${base}${path}`, {
    credentials: 'same-origin',
    ...init,
    headers: {
      'X-Core-Contract-Version': HTTP_CONTRACT_VERSION,
      Accept: 'application/json',
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
  });
  if (!res.ok) throw await apiErrorFrom(res);
  return res.json() as Promise<T>;
}
