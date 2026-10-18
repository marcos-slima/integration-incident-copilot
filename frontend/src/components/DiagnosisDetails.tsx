// Detalhes do diagnostico que o backend ja devolve e a tela nao mostrava:
// sinal de escalonamento (DA-44), evidencias com nivel de confianca
// (DA-25, DA-71) e proveniencia (DA-53). Tudo texto - nada aqui
// interpreta HTML vindo do backend.

import type {
  DiagnosisResponse,
  EscalationSignal,
  EvidenceItem,
} from '../types/models';

const TRUST_LABELS: Record<EvidenceItem['trust_level'], { label: string; cls: string }> = {
  system_observed: { label: 'observado no sistema', cls: 'trust-ok' },
  retrieved_document: { label: 'documento da base', cls: 'trust-doc' },
  web_untrusted: { label: 'web · não confiável', cls: 'trust-warn' },
  user_reported: { label: 'relato do usuário', cls: 'trust-neutral' },
  simulated: { label: 'simulado (demo)', cls: 'trust-warn' },
};

const ESCALATION_REASONS: Record<string, string> = {
  grounded: 'Evidência suficiente na base curada.',
  no_context: 'Sem contexto: nenhum dado de conector nem documento recuperado.',
  abstained: 'Os guardrails anularam a fonte: a resposta não tem lastro.',
  floor_tier_weak: 'Só manuais genéricos sustentam a resposta, e com evidência fraca.',
  curated_tier_weak: 'Há documento curado, mas o reranker não sustentou a correspondência.',
};

const EXCERPT_MAX = 280;

export function EscalationBanner({ signal }: { signal?: EscalationSignal }) {
  if (!signal) return null;
  const reason = ESCALATION_REASONS[signal.escalation] ?? signal.escalation;
  if (!signal.should_escalate) {
    return (
      <div className="escalation escalation-ok" role="status">
        <span className="escalation-title">Evidência suficiente</span>
        <span className="escalation-text">{reason}</span>
      </div>
    );
  }
  return (
    <div className="escalation escalation-warn" role="alert">
      <span className="escalation-title">Revisão humana recomendada</span>
      <span className="escalation-text">
        {reason} Evidência principal: {Math.round(signal.top_evidence * 100)}%
        {signal.connector_real ? ' · dado real de conector' : ' · sem dado real de conector'}.
      </span>
      <span className="escalation-note">
        Sinal informativo: nenhum modelo pago foi acionado por causa dele.
      </span>
    </div>
  );
}

export function EvidenceList({ evidence }: { evidence?: EvidenceItem[] }) {
  if (!evidence?.length) return null;
  const simulated = evidence.filter((e) => e.trust_level === 'simulated').length;
  const web = evidence.filter((e) => e.trust_level === 'web_untrusted').length;
  return (
    <div className="evidence-section">
      <div className="section-label">Evidências ({evidence.length})</div>
      {(simulated > 0 || web > 0) && (
        <p className="evidence-warn">
          {simulated > 0 && `${simulated} de dado simulado (conector em modo demonstração). `}
          {web > 0 && `${web} da web, tratada como não confiável: confirme na fonte.`}
        </p>
      )}
      <ul className="evidence-list">
        {evidence.map((e, i) => {
          const t = TRUST_LABELS[e.trust_level] ?? { label: e.trust_level, cls: 'trust-neutral' };
          const excerpt =
            e.excerpt.length > EXCERPT_MAX ? `${e.excerpt.slice(0, EXCERPT_MAX)}…` : e.excerpt;
          return (
            <li key={i} className="evidence-item">
              <div className="evidence-head">
                <span className={`trust-chip ${t.cls}`}>{t.label}</span>
                <span className="evidence-source">
                  {e.source_type}:{e.source_id}
                  {e.locator ? ` · ${e.locator}` : ''}
                </span>
                {e.rerank_score != null && (
                  <span className="evidence-score">rerank {e.rerank_score.toFixed(2)}</span>
                )}
              </div>
              {excerpt && <p className="evidence-excerpt">{excerpt}</p>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function Provenance({ result }: { result: DiagnosisResponse }) {
  const ruled = result.llm_provider_used === 'rule_engine';
  const parts: string[] = ruled
    ? ['Regra determinística', 'sem LLM', 'custo zero']
    : [
        result.llm_provider_used ?? 'provedor desconhecido',
        ...(result.llm_model ? [result.llm_model] : []),
        ...(result.prompt_version ? [`prompt ${result.prompt_version}`] : []),
      ];
  if (result.agent_domain) parts.push(`domínio ${result.agent_domain}`);
  return (
    <div className="provenance">
      <span className="provenance-label">Proveniência</span>
      <span className="provenance-text">{parts.join(' · ')}</span>
      {result.prompt_digest && (
        <span className="provenance-digest" title={result.prompt_digest}>
          {result.prompt_digest.slice(0, 12)}
        </span>
      )}
      {result.incident_id && (
        <span className="provenance-digest" title="incident_id">
          {result.incident_id}
        </span>
      )}
    </div>
  );
}
