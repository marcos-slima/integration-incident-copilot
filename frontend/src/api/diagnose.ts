// Camada de comunicação com o backend FastAPI.
// Isola o fetch() do resto da aplicação —
// se a URL ou o formato mudar, só este arquivo precisa mudar.

import type { DiagnosisResponse, IncidentRequest } from '../types/models';

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
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
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

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new ApiError(res.status, err.detail ?? `HTTP ${res.status}`);
  }

  return res.json() as Promise<DiagnosisResponse>;
}
