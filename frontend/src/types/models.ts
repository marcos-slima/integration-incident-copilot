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
  | 'apim'
  | 'po';

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
  locale?: 'pt-BR' | 'en'; // DA-74: idioma do conteudo do diagnostico
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
  locale?: 'pt-BR' | 'en'; // DA-74: idioma em que o diagnostico foi produzido
  evidence?: EvidenceItem[];
  incident_id?: string;
  trace_id?: string;
  escalation?: EscalationSignal;
}

// espelho de app/agent/escalation.py::EscalationDecision.as_log_fields (DA-44).
// INFORMATIVO: o pipeline nao escala sozinho para um modelo pago.
export type EscalationReason =
  | 'grounded'
  | 'no_context'
  | 'abstained'
  | 'floor_tier_weak'
  | 'curated_tier_weak';

export interface EscalationSignal {
  escalation: EscalationReason | string; // codigo da razao
  should_escalate: boolean;
  tier: string;
  abstained: boolean;
  top_evidence: number;
  connector_real: boolean;
}

// Corpo de erro da API: `code` e estavel (app/main.py, handlers de excecao);
// `error_id` liga a resposta ao log do servidor.
export type ApiErrorCode =
  | 'budget_exhausted' // DA-67
  | 'policy_violation' // DA-66/DA-43
  | 'overloaded'
  | 'timeout'
  | 'internal_error';

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
