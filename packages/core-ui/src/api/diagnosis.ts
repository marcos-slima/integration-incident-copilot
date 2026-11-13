// Chamadas de diagnostico do core (DA-95).
import i18n from 'i18next';
import { apiJson, ApiError, HTTP_CONTRACT_VERSION } from './http';
import type { CoreIncidentRequest, DiagnosisResponse, DomainPluginInfo } from './contracts';

/** POST /diagnose. `T` e' o request do shell (estende o do core). */
export function diagnose<T extends CoreIncidentRequest>(request: T): Promise<DiagnosisResponse> {
  return apiJson<DiagnosisResponse>('/diagnose', { method: 'POST', body: JSON.stringify(request) });
}

export type HumanReviewAction = 'approve' | 'edit' | 'reject';

export function reviewIncident(
  incidentId: string,
  action: HumanReviewAction,
  edit?: string,
): Promise<{ incident_id: string; status: 'reviewed'; graph_updated: boolean }> {
  return apiJson(`/incidents/${encodeURIComponent(incidentId)}/review`, {
    method: 'POST',
    body: JSON.stringify({ action, edit: action === 'edit' ? edit : undefined, reviewed_by: 'human' }),
  });
}

/** GET /domain-plugins: plugins carregados e o schema de contexto de cada um. */
export async function getDomainPlugins(): Promise<DomainPluginInfo[]> {
  const body = await apiJson<{ contract_version?: string; plugins: DomainPluginInfo[] }>('/domain-plugins');
  // Catálogos anteriores à versão explícita pertencem ao contrato 1.0.
  const version = body.contract_version ?? '1.0';
  const [major, minor] = version.split('.').map(Number);
  const [supportedMajor, supportedMinor] = HTTP_CONTRACT_VERSION.split('.').map(Number);
  if (!/^\d+\.\d+$/.test(version) || major !== supportedMajor || minor > supportedMinor) {
    throw new ApiError(409, i18n.t('core.errors.incompatible_contract'), 'incompatible_contract');
  }
  return body.plugins;
}
