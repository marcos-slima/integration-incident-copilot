// Contratos HTTP do core (DA-95) - espelho de app/models/models.py.
// So o que e' do core: nenhum campo de produto. Cada shell estende
// `CoreIncidentRequest` com os campos de entrada que usa.

export type ContentLocale = 'pt-BR' | 'en';

// `code` estavel do corpo de erro (app/main.py, handlers de excecao)
export type ApiErrorCode =
  | 'budget_exhausted' // DA-67
  | 'policy_violation' // DA-66/DA-43
  | 'overloaded'
  | 'timeout'
  | 'internal_error'
  | 'domain_context_invalid'; // DA-94

/** Campos de IncidentRequest comuns a qualquer produto. */
export interface CoreIncidentRequest {
  description: string;
  logs?: string;
  payload?: string;
  locale?: ContentLocale; // DA-74
  domain_context?: Record<string, unknown>; // DA-94: opaco para o core
}

// app/models.py::Evidence - proveniencia de cada afirmacao
export interface EvidenceItem {
  source_id: string;
  source_type: 'connector' | 'rag' | 'graph' | 'web' | 'user' | 'rule_engine';
  locator?: string | null;
  excerpt: string;
  retrieval_score?: number | null;
  rerank_score?: number | null;
  trust_level:
    | 'system_observed'
    | 'rule_reference'
    | 'retrieved_document'
    | 'web_untrusted'
    | 'user_reported'
    | 'simulated';
}

// app/models.py::Finding (DA-93): `fact` so vem de ferramenta, conector ou
// regra; a causa proposta e' sempre `hypothesis`.
export interface Finding {
  id: string;
  kind: 'fact' | 'hypothesis';
  source: 'tool' | 'connector' | 'rule_reference' | 'rag' | 'llm';
  statement: string;
  evidence_refs?: string[];
  confirmation_test?: string[];
  alternatives?: string[];
  rule_ref?: string;
}

export type CauseStatus = 'hypothesis' | 'confirmed' | 'inconclusive';

// app/agent/escalation.py::EscalationDecision.as_log_fields (DA-44)
export type EscalationReason =
  | 'grounded'
  | 'no_context'
  | 'abstained'
  | 'floor_tier_weak'
  | 'curated_tier_weak';

export interface EscalationSignal {
  escalation: EscalationReason | string;
  should_escalate: boolean;
  tier: string;
  abstained: boolean;
  top_evidence: number;
  connector_real: boolean;
}

// app/models.py::DiagnosisResponse. Campos nullable chegam AUSENTES do JSON
// (exclude_none): por isso sao opcionais aqui.
export interface DiagnosisResponse {
  probable_root_cause: string;
  model_confidence: number;
  diagnosis_confidence: number;
  next_steps: string[];
  report_markdown: string;
  matched_source?: string;
  evidence_strength?: number;
  llm_provider_used?: string;
  agent_domain?: string;
  llm_model?: string;
  prompt_version?: string;
  prompt_digest?: string;
  locale?: ContentLocale;
  evidence?: EvidenceItem[];
  findings?: Finding[];
  cause_status?: CauseStatus;
  incident_id?: string;
  trace_id?: string;
  escalation?: EscalationSignal;
  ai_warning?: string;
  explanation?: string;
}

// --- GET /domain-plugins (DA-95) ---------------------------------------------

/** Subconjunto de JSON Schema que o SchemaForm sabe renderizar. */
export interface JsonSchema {
  type?: 'object' | 'string' | 'array' | 'integer' | 'number' | 'boolean' | 'null';
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  enum?: unknown[];
  const?: unknown;
  default?: unknown;
  pattern?: string;
  format?: string;
  maxLength?: number;
  maxItems?: number;
  anyOf?: JsonSchema[];
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
}

export interface DomainPluginInfo {
  name: string;
  context_schema: JsonSchema;
  prompt_version: string | null;
}
