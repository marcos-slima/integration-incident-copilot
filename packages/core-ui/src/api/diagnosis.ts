// Chamadas de diagnostico do core (DA-95).
import { apiJson } from './http';
import type { CoreIncidentRequest, DiagnosisResponse, DomainPluginInfo } from './contracts';

/** POST /diagnose. `T` e' o request do shell (estende o do core). */
export function diagnose<T extends CoreIncidentRequest>(request: T): Promise<DiagnosisResponse> {
  return apiJson<DiagnosisResponse>('/diagnose', { method: 'POST', body: JSON.stringify(request) });
}

/** GET /domain-plugins: plugins carregados e o schema de contexto de cada um. */
export async function getDomainPlugins(): Promise<DomainPluginInfo[]> {
  const body = await apiJson<{ plugins: DomainPluginInfo[] }>('/domain-plugins');
  return body.plugins;
}
