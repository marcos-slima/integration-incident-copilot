// Espelho exato dos modelos Pydantic em app/models.py
// Se o backend mudar, este arquivo muda junto.

export type InterfaceType =
  | 'odata'
  | 'rfc'
  | 'servicenow'
  | 'salesforce'
  | 'workday'
  | 'ariba'
  | 'successfactors'
  | 'cap'
  | 'apim';

export interface IncidentRequest {
  description: string;
  logs?: string;
  payload?: string;
  interface_type?: InterfaceType;
  identifier?: string;
  connector_source_system?: string;
  sensitivity_level?: 'public' | 'internal' | 'confidential' | 'secret';
  pii_detected?: boolean;
  redaction_applied?: boolean;
}

// espelho de app/models.py::Evidence — proveniência de cada afirmação
export interface EvidenceItem {
  source_id: string;
  source_type: 'connector' | 'rag' | 'graph' | 'web' | 'user' | 'rule_engine';
  locator?: string | null;
  excerpt: string;
  retrieval_score?: number | null;
  rerank_score?: number | null;
  trust_level:
    | 'system_observed'
    | 'retrieved_document'
    | 'web_untrusted'
    | 'user_reported'
    | 'simulated';
}

// espelho de app/models.py::DiagnosisResponse. Campos nullable chegam
// AUSENTES do JSON (exclude_none) — por isso são opcionais aqui, e não
// `| null`. `llm_model`/`prompt_version`/`prompt_digest` ficam de fora
// quando a rule engine respondeu sem LLM (DA-53, invariante 21).
export interface DiagnosisResponse {
  probable_root_cause: string;
  model_confidence: number; // o que o LLM disse de si
  diagnosis_confidence: number; // pós-guardrails — o que o badge mostra
  next_steps: string[];
  report_markdown: string;
  matched_source?: string;
  evidence_strength?: number;
  llm_provider_used?: string;
  agent_domain?: string;
  llm_model?: string;
  prompt_version?: string;
  prompt_digest?: string;
  evidence?: EvidenceItem[];
  incident_id?: string;
  trace_id?: string;
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
