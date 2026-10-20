// Camada de comunicação com GET /health - estado real dos conectores e
// da infraestrutura, derivado do .env atual do backend (ver
// app.connectors.connector_status). Consumido por StatusView.

import type { HealthResponse, LlmPolicy } from '../types/models';
import { apiErrorFrom } from './diagnose';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

export async function callHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE}/health`);

  if (!res.ok) throw await apiErrorFrom(res);

  return res.json() as Promise<HealthResponse>;
}

// GET /llm/policy (DA-43/DA-66): para onde o dado pode ir, por provider.
// Exige sessao (cookie) ou X-API-Key - nunca devolve credencial, so origens.
export async function callLlmPolicy(): Promise<LlmPolicy> {
  const res = await fetch(`${API_BASE}/llm/policy`, { credentials: 'same-origin' });
  if (!res.ok) throw await apiErrorFrom(res);
  return res.json() as Promise<LlmPolicy>;
}
