// Tipos do produto IIC. Os contratos do core (DiagnosisResponse, Finding,
// EvidenceItem...) vem de @copilot/core-ui (DA-95); aqui so o que e' do IIC.
import type { CoreIncidentRequest, DiagnosisResponse } from '@copilot/core-ui';

export type { DiagnosisResponse };

export type InterfaceType =
  | 'odata'
  | 'rfc'
  | 'servicenow'
  | 'salesforce'
  | 'workday'
  | 'ariba'
  | 'successfactors'
  | 'cap'
  | 'apim'
  | 'po';

// Request do IIC = campos do core + os de conector (so deste produto).
export interface IncidentRequest extends CoreIncidentRequest {
  interface_type?: InterfaceType;
  identifier?: string;
  connector_source_system?: string;
  sensitivity_level?: 'public' | 'internal' | 'confidential' | 'secret';
  pii_detected?: boolean;
  redaction_applied?: boolean;
}

// Espelho de GET /llm/policy (app/llm/routing.py::describe_effective_policy)
export interface ProviderPolicy {
  locality: 'local' | 'cloud';
  origin: string | null;
  may_receive_public: boolean;
  may_receive_confidential: boolean;
  public_reason: string;
  confidential_reason: string;
  // DA-74: motivo com codigo estavel, traduzido pela UI (sovereigntyReason.*)
  public_reason_code?: string;
  public_reason_params?: Record<string, string>;
  confidential_reason_code?: string;
  confidential_reason_params?: Record<string, string>;
}

export interface LlmPolicy {
  data_sovereignty_mode: string;
  sensitivity_default: string;
  confidential_allowed_origins: string[];
  routing: {
    primary: string;
    fallback: string | null;
    allowed_for_confidential: string[];
    allowed_for_public: string[];
  };
  limits: { max_cost_usd: number; request_timeout_seconds: number };
  providers: Record<string, ProviderPolicy>;
  note: string;
}

// Tipo interno da UI — não existe no backend
export interface HistoryItem {
  description: string;
  interface_type: string;
  identifier: string;
  result: DiagnosisResponse;
  ts: Date;
}

// Espelho de GET /health (app/main.py::health / app.connectors.connector_status)
export type ConnectorHealthStatus = 'real' | 'mock' | 'misconfigured';

export interface ConnectorHealth {
  status: ConnectorHealthStatus;
  note: string;
  setting?: string; // DA-74: variavel de modo real, para o texto traduzido
}

export interface HealthResponse {
  status: string;
  connectors: Record<InterfaceType, ConnectorHealth>;
  infra: {
    llm_provider: string;
    graph_rag_enabled: boolean;
    langfuse_enabled: boolean;
    async_queue_enabled: boolean;
    auth_required: boolean;
  };
}
