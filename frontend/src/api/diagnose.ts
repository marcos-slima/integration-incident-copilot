// Camada de comunicação com o backend FastAPI.
// Isola o fetch() do resto da aplicação —
// se a URL ou o formato mudar, só este arquivo precisa mudar.

import type { ApiErrorCode, DiagnosisResponse, IncidentRequest } from '../types/models';

// Em desenvolvimento, o Vite proxy redireciona /diagnose para localhost:8000
// Em produção, usa a mesma origem (FastAPI serve o frontend)
const API_BASE = import.meta.env.VITE_API_URL ?? '';

// Autenticacao: cookie de sessao HttpOnly emitido por POST /auth/login (DA-54).
// O navegador envia o cookie sozinho (mesma origem; no dev, via proxy do Vite).
// A leitura de uma X-API-Key da sessionStorage foi removida (FE-01,
// validacao 2026-10-07): era o mecanismo anterior ao login e nenhuma tela
// preenchia a chave. Integracoes de maquina usam X-API-Key direto na API.

export class ApiError extends Error {
  status: number;
  code?: ApiErrorCode | string; // app/main.py: budget_exhausted, policy_violation...
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

// Mensagem para o usuario, por `code` quando existe e por status no resto.
export function describeApiError(e: ApiError): string {
  const ref = e.errorId ? ` (ref. ${e.errorId})` : '';
  switch (e.code) {
    case 'budget_exhausted':
      return `Orçamento do diagnóstico esgotado: nenhum provedor elegível cabe no limite configurado. Ajuste DIAGNOSIS_BUDGET_USD ou habilite um provedor local.${ref}`;
    case 'policy_violation':
      return `A política de soberania não permite nenhum provedor para a sensibilidade deste incidente. Veja "Soberania do dado" em Status.${ref}`;
    case 'overloaded':
      return 'Capacidade de diagnóstico esgotada no momento. Tente de novo em alguns segundos.';
    case 'timeout':
      return `O diagnóstico excedeu o tempo limite. ${e.message}`;
  }
  if (e.status === 422) return `Dados inválidos: ${e.message}`;
  if (e.status === 429) return 'Limite de requisições atingido. Aguarde e tente de novo.';
  return `Erro ${e.status}: ${e.message}${ref}`;
}

export async function callDiagnose(
  payload: IncidentRequest,
): Promise<DiagnosisResponse> {
  const res = await fetch(`${API_BASE}/diagnose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(payload),
  });

  if (!res.ok) throw await apiErrorFrom(res);

  return res.json() as Promise<DiagnosisResponse>;
}
