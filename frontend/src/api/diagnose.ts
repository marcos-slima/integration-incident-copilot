// Camada de comunicação com o backend FastAPI.
// Isola o fetch() do resto da aplicação —
// se a URL ou o formato mudar, só este arquivo precisa mudar.

import type { DiagnosisResponse, IncidentRequest } from '../types/models';

// Em desenvolvimento, o Vite proxy redireciona /diagnose para localhost:8000
// Em produção, usa a mesma origem (FastAPI serve o frontend)
const API_BASE = import.meta.env.VITE_API_URL ?? '';

// A4: VITE_API_KEY foi REMOVIDA do build do Vite.
//
// Motivo: variáveis VITE_* são embutidas no bundle JavaScript pelo Vite
// em tempo de build — qualquer usuário do frontend pode extrair a chave
// inspecionando o bundle (DevTools → Sources ou strings no .js).
// Uma API key no bundle NÃO é um segredo; é equivalente a não ter auth.
//
// Alternativa correta: a chave é lida em tempo de execução da sessionStorage
// (preenchida pelo usuário na UI, nunca no código-fonte ou em variáveis de
// ambiente de build). Em produção, considere migrar para autenticação via
// cookie de sessão obtido através de um endpoint /auth/login dedicado —
// essa evolução não exige mudança neste arquivo, só adicionar o endpoint
// no backend e chamar document.cookie no lugar de sessionStorage.
const STORAGE_KEY = 'integration_copilot_api_key';

/**
 * Lê a API key configurada pelo usuário em runtime (sessionStorage).
 * Retorna string vazia se não configurada (modo sem autenticação).
 */
export function getApiKey(): string {
  try {
    return sessionStorage.getItem(STORAGE_KEY) ?? '';
  } catch {
    // sessionStorage pode estar bloqueado em modo privado
    return '';
  }
}

/**
 * Persiste a API key na sessionStorage (válida apenas na aba/sessão atual).
 * Chame a partir de um componente de configuração onde o usuário digita a chave.
 */
export function setApiKey(key: string): void {
  try {
    if (key) {
      sessionStorage.setItem(STORAGE_KEY, key);
    } else {
      sessionStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // sessionStorage bloqueado — ignora silenciosamente
  }
}

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
  const apiKey = getApiKey();
  if (apiKey) {
    headers['X-API-Key'] = apiKey;
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
