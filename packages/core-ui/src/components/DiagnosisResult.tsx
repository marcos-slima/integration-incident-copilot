// DiagnosisResult (DA-95): cartao de resultado de qualquer produto. Mostra
// so campos do contrato do core (DiagnosisResponse). O que e' do produto
// (rotulos de dominio, acoes) entra por `extra`, abaixo da causa raiz.

import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import type { DiagnosisResponse } from '../api/contracts';
import { Badge } from './Badge';
import { EscalationBanner, EvidenceList, Provenance } from './DiagnosisDetails';
import { FindingsPanel } from './FindingsPanel';

interface DiagnosisResultProps {
  result: DiagnosisResponse;
  extra?: ReactNode;
}

export function DiagnosisResult({ result, extra }: DiagnosisResultProps) {
  const { t } = useTranslation();
  const [showReport, setShowReport] = useState(false);
  return (
    <div className="result-card">
      <div className="result-header">
        <span className="result-header-title">{t('core.result.resultTitle')}</span>
        <Badge value={result.diagnosis_confidence} />
      </div>
      <div className="result-body">
        <EscalationBanner signal={result.escalation} />

        <div className="section-label">{t('core.result.rootCause')}</div>
        <p className="root-cause">{result.probable_root_cause}</p>
        {extra}

        {result.explanation && (
          <div className="explanation-section">
            <div className="section-label">{t('core.result.explanation')}</div>
            <p className="explanation-text">{result.explanation}</p>
          </div>
        )}

        {result.ai_warning && (
          <p className="ai-warning" role="note">
            {result.ai_warning}
          </p>
        )}

        <FindingsPanel findings={result.findings} causeStatus={result.cause_status} />

        <div className="section-label">{t('core.result.nextSteps')}</div>
        <ol className="steps-list">
          {result.next_steps.map((step, i) => (
            <li key={i} className="step-item">
              <span className="step-num">{i + 1}</span>
              <span className="step-text">{step}</span>
            </li>
          ))}
        </ol>

        {result.matched_source && (
          <div className="ref-doc">
            <span className="ref-label">{t('core.result.referenceDoc')}</span>
            <span className="ref-name">{result.matched_source}</span>
          </div>
        )}

        <EvidenceList evidence={result.evidence} />
        <Provenance result={result} />

        {result.report_markdown && (
          <div className="report-section">
            <button className="report-toggle" onClick={() => setShowReport((v) => !v)}>
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path
                  d={showReport ? 'M2 4l4 4 4-4' : 'M4 2l4 4-4 4'}
                  stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"
                />
              </svg>
              {showReport ? t('core.result.reportHide') : t('core.result.reportShow')}
            </button>
            {showReport && (
              // XSS (P1 da avaliacao externa): react-markdown NAO interpreta
              // HTML embutido (sem rehype-raw, de proposito) - so Markdown.
              <div className="report-body">
                <ReactMarkdown>{result.report_markdown}</ReactMarkdown>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Cartao de "processando" padrao. */
export function DiagnosisLoading() {
  const { t } = useTranslation();
  return (
    <div className="loading-card">
      <div className="spinner spinner-lg" />
      <div className="loading-title">{t('core.result.processingTitle')}</div>
      <div className="loading-sub">{t('core.result.processingSub')}</div>
    </div>
  );
}
