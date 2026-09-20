// Camada de comunicação com GET /health - estado real dos conectores e
// da infraestrutura, derivado do .env atual do backend (ver
// app.connectors.connector_status). Consumido por StatusView.

import type { HealthResponse } from '../types/models';
import { ApiError } from './diagnose';

const API_BASE = import.meta.env.VITE_API_URL ?? '';

export async function callHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE}/health`);

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new ApiError(res.status, err.detail ?? `HTTP ${res.status}`);
  }

  return res.json() as Promise<HealthResponse>;
}
