// Camada de comunicação com o backend FastAPI.
// Isola o fetch() do resto da aplicação —
// se a URL ou o formato mudar, só este arquivo precisa mudar.

import type { DiagnosisResponse, IncidentRequest } from '../types/models';

// Em desenvolvimento, o Vite proxy redireciona /diagnose para localhost:8000
// Em produção, usa a mesma origem (FastAPI serve o frontend)
const API_BASE = import.meta.env.VITE_API_URL ?? '';

// Se o backend estiver com REQUIRE_AUTH=true / API_KEY configurada (ver
// .env.example), toda chamada a /diagnose exige o header X-API-Key - sem
// isso o backend devolve 401 e a UI nunca funciona. VITE_API_KEY e opcional:
// deployments sem autenticacao (API_KEY vazia no backend) funcionam sem ela.
const API_KEY = import.meta.env.VITE_API_KEY ?? '';

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
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (API_KEY) {
    headers['X-API-Key'] = API_KEY;
  }

  const res = await fetch(`${API_BASE}/diagnose`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new ApiError(res.status, err.detail ?? `HTTP ${res.status}`);
  }

  return res.json() as Promise<DiagnosisResponse>;
}
