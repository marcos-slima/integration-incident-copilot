// Espelho exato dos modelos Pydantic em app/models.py
// Se o backend mudar, este arquivo muda junto.

export type InterfaceType =
  | 'odata'
  | 'rfc'
  | 'servicenow'
  | 'salesforce'
  | 'workday'
  | 'ariba'
  | 'cap'
  | 'apim';

export interface IncidentRequest {
  description: string;
  interface_type?: InterfaceType;
  identifier?: string;
  logs?: string;
  payload?: string;
}

export interface DiagnosisResponse {
  probable_root_cause: string;
  confidence: number;        // 0.0 a 1.0
  next_steps: string[];
  report_markdown: string;
  matched_source: string | null;
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
