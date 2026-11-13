// FindingsPanel (DA-93 + DA-95): fatos x hipoteses de qualquer produto.
// O core nao interpreta o id do achado (convencao do produto); so separa
// `fact` de `hypothesis` e mostra fonte, evidencias e teste de confirmacao.

import { useTranslation } from 'react-i18next';
import type { CauseStatus, Finding } from '../api/contracts';

const SOURCES = ['tool', 'connector', 'rule_reference', 'rag', 'llm'];
const STATUS_CLASS: Record<CauseStatus, string> = {
  confirmed: 'trust-ok',
  hypothesis: 'trust-doc',
  inconclusive: 'trust-warn',
};

function FindingItem({ f }: { f: Finding }) {
  const { t } = useTranslation();
  const source = SOURCES.includes(f.source) ? t(`core.findings.source.${f.source}`) : f.source;
  return (
    <li className={`finding finding-${f.kind}`}>
      <div className="finding-head">
        <span className="finding-id">{f.id}</span>
        <span className="finding-source">{source}</span>
        {f.rule_ref && <span className="finding-rule">{f.rule_ref}</span>}
      </div>
      <p className="finding-statement">{f.statement}</p>
      {!!f.evidence_refs?.length && (
        <div className="finding-meta">
          {t('core.findings.evidence')}{' '}
          {f.evidence_refs.map((r) => (
            <code key={r} className="finding-ref">{r}</code>
          ))}
        </div>
      )}
      {!!f.confirmation_test?.length && (
        <div className="finding-meta">
          {t('core.findings.confirmWith')}
          <ul className="finding-sublist">
            {f.confirmation_test.map((c) => <li key={c}>{c}</li>)}
          </ul>
        </div>
      )}
      {!!f.alternatives?.length && (
        <div className="finding-meta">
          {t('core.findings.alternatives')} {f.alternatives.join(' · ')}
        </div>
      )}
    </li>
  );
}

export function FindingsPanel({ findings, causeStatus }: { findings?: Finding[]; causeStatus?: CauseStatus }) {
  const { t } = useTranslation();
  if (!findings?.length) return null;
  const facts = findings.filter((f) => f.kind === 'fact');
  const hypotheses = findings.filter((f) => f.kind === 'hypothesis');
  return (
    <div className="findings-section">
      <div className="findings-title">
        <span className="section-label">{t('core.findings.title')}</span>
        {causeStatus && (
          <span className={`trust-chip ${STATUS_CLASS[causeStatus]}`}>
            {t(`core.findings.status.${causeStatus}`)}
          </span>
        )}
      </div>
      <div className="findings-grid">
        <div>
          <div className="findings-col-title">{t('core.findings.facts', { count: facts.length })}</div>
          {facts.length ? (
            <ul className="findings-list">{facts.map((f) => <FindingItem key={f.id} f={f} />)}</ul>
          ) : (
            <p className="findings-empty">{t('core.findings.noFacts')}</p>
          )}
        </div>
        <div>
          <div className="findings-col-title">{t('core.findings.hypotheses', { count: hypotheses.length })}</div>
          {hypotheses.length ? (
            <ul className="findings-list">{hypotheses.map((f) => <FindingItem key={f.id} f={f} />)}</ul>
          ) : (
            <p className="findings-empty">{t('core.findings.noHypotheses')}</p>
          )}
        </div>
      </div>
    </div>
  );
}
