// Detalhes do diagnostico que o backend ja devolve: sinal de escalonamento
// (DA-44), evidencias com nivel de confianca (DA-25, DA-71) e proveniencia
// (DA-53). DA-73: rotulos traduzidos pelo CODIGO (trust_level, motivo do
// sinal), nunca pelo texto do backend. Nada aqui interpreta HTML.

import { useTranslation } from 'react-i18next';
import { formatNumber, formatPercent } from '../i18n/format';
import type { DiagnosisResponse, EscalationSignal, EvidenceItem } from '../types/models';

const TRUST_CLASS: Record<EvidenceItem['trust_level'], string> = {
  system_observed: 'trust-ok',
  retrieved_document: 'trust-doc',
  web_untrusted: 'trust-warn',
  user_reported: 'trust-neutral',
  simulated: 'trust-warn',
};

const KNOWN_REASONS = ['grounded', 'no_context', 'abstained', 'floor_tier_weak', 'curated_tier_weak'];
const EXCERPT_MAX = 280;

export function EscalationBanner({ signal }: { signal?: EscalationSignal }) {
  const { t } = useTranslation();
  if (!signal) return null;
  const reason = KNOWN_REASONS.includes(signal.escalation)
    ? t(`details.reasons.${signal.escalation}`)
    : signal.escalation;
  if (!signal.should_escalate) {
    return (
      <div className="escalation escalation-ok" role="status">
        <span className="escalation-title">{t('details.okTitle')}</span>
        <span className="escalation-text">{reason}</span>
      </div>
    );
  }
  return (
    <div className="escalation escalation-warn" role="alert">
      <span className="escalation-title">{t('details.warnTitle')}</span>
      <span className="escalation-text">
        {t('details.warnText', {
          reason,
          pct: formatPercent(signal.top_evidence),
          connector: signal.connector_real
            ? t('details.connectorReal')
            : t('details.connectorNotReal'),
        })}
      </span>
      <span className="escalation-note">{t('details.note')}</span>
    </div>
  );
}

export function EvidenceList({ evidence }: { evidence?: EvidenceItem[] }) {
  const { t } = useTranslation();
  if (!evidence?.length) return null;
  const simulated = evidence.filter((e) => e.trust_level === 'simulated').length;
  const web = evidence.filter((e) => e.trust_level === 'web_untrusted').length;
  return (
    <div className="evidence-section">
      <div className="section-label">{t('details.evidenceTitle', { count: evidence.length })}</div>
      {(simulated > 0 || web > 0) && (
        <p className="evidence-warn">
          {simulated > 0 && t('details.simulated', { count: simulated })}{' '}
          {web > 0 && t('details.web', { count: web })}
        </p>
      )}
      <ul className="evidence-list">
        {evidence.map((e, i) => {
          const cls = TRUST_CLASS[e.trust_level] ?? 'trust-neutral';
          const label = TRUST_CLASS[e.trust_level] ? t(`details.trust.${e.trust_level}`) : e.trust_level;
          const excerpt =
            e.excerpt.length > EXCERPT_MAX ? `${e.excerpt.slice(0, EXCERPT_MAX)}…` : e.excerpt;
          return (
            <li key={i} className="evidence-item">
              <div className="evidence-head">
                <span className={`trust-chip ${cls}`}>{label}</span>
                <span className="evidence-source">
                  {e.source_type}:{e.source_id}
                  {e.locator ? ` · ${e.locator}` : ''}
                </span>
                {e.rerank_score != null && (
                  <span className="evidence-score">
                    {t('details.rerank', { score: formatNumber(e.rerank_score) })}
                  </span>
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
  const { t } = useTranslation();
  const ruled = result.llm_provider_used === 'rule_engine';
  const parts: string[] = ruled
    ? [t('details.ruleEngine'), t('details.noLlm'), t('details.zeroCost')]
    : [
        result.llm_provider_used ?? t('details.unknownProvider'),
        ...(result.llm_model ? [result.llm_model] : []),
        ...(result.prompt_version ? [t('details.prompt', { version: result.prompt_version })] : []),
      ];
  if (result.agent_domain) parts.push(t('details.domain', { domain: result.agent_domain }));
  return (
    <div className="provenance">
      <span className="provenance-label">{t('details.provenance')}</span>
      <span className="provenance-text">{parts.join(' · ')}</span>
      {result.prompt_digest && (
        <span className="provenance-digest" title={result.prompt_digest}>
          {result.prompt_digest.slice(0, 12)}
        </span>
      )}
      {result.incident_id && (
        <span className="provenance-digest" title={'incident_id' /* i18n-ignore: nome de campo */}>
          {result.incident_id}
        </span>
      )}
    </div>
  );
}
