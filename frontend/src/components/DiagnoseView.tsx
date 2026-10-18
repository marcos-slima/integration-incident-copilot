// DiagnoseView — formulário principal de diagnóstico.
//
// CONCEITOS NOVOS:
// - useRef<T>: referência tipada para elementos DOM
// - async function com try/catch tipado
// - useState<T | null>: estado que pode ser null
// - FileReader API com TypeScript

import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import { callDiagnose, ApiError, describeApiError } from '../api/diagnose';
import type {
  DiagnosisResponse,
  HistoryItem,
  IncidentRequest,
  InterfaceType,
} from '../types/models';
import { Badge } from './Badge';
import { EscalationBanner, EvidenceList, Provenance } from './DiagnosisDetails';

// Props do componente
interface DiagnoseViewProps {
  onResult: (item: HistoryItem) => void; // callback para adicionar ao histórico
  onSessionExpired: () => void; // DA-54: 401 devolve para a tela de login
}

// Sistemas disponíveis no dropdown — os 9 conectores do pipeline
// (app/connectors/__init__.py::_REGISTRY). O gate connector_reachable
// reprova se um conector registrado faltar aqui.
// DA-73: o segundo elemento e' a chave do rotulo no catalogo.
const SYSTEMS: Array<[string, string]> = [
  ['', 'systems.none'],
  ['odata', 'systems.odata'],
  ['rfc', 'systems.rfc'],
  ['servicenow', 'systems.servicenow'],
  ['salesforce', 'systems.salesforce'],
  ['workday', 'systems.workday'],
  ['ariba', 'systems.ariba'],
  ['successfactors', 'systems.successfactors'],
  ['cap', 'systems.cap'],
  ['apim', 'systems.apim'],
  ['po', 'systems.po'],
];

// Limite de tamanho de arquivo p/ upload - alinhado ao limite do backend
// para logs/payload (MAX_LOGS_LENGTH/MAX_PAYLOAD_LENGTH em app/models.py,
// 50.000 caracteres). Usamos bytes do arquivo (file.size) como proxy do
// numero de caracteres: em UTF-8 um caractere nunca ocupa menos de 1 byte,
// entao um arquivo com ate MAX_UPLOAD_BYTES bytes sempre tem no maximo
// MAX_UPLOAD_BYTES caracteres - um arquivo aceito aqui nunca estoura o
// limite da API (422), mesmo com acentos/caracteres multi-byte.
const MAX_UPLOAD_BYTES = 50_000;

// Identificadores de demonstração
// (desc = chave do catalogo; o texto vai no idioma da UI)
const DEMO_IDS = [
  { id: 'CPI-401-DEMO',          sys: 'odata', desc: 'demo.cpi401' },
  { id: 'RFC-IDOC-51-DEMO',      sys: 'rfc',   desc: 'demo.idoc51' },
  { id: 'RFC-CONN-REFUSED-DEMO', sys: 'rfc',   desc: 'demo.connRefused' },
];

// Tipo do arquivo anexado
interface UploadedFile {
  name: string;
  content: string;
  target: 'logs' | 'payload';
}

export function DiagnoseView({ onResult, onSessionExpired }: DiagnoseViewProps) {
  const { t } = useTranslation();
  // Estado do formulário
  const [desc, setDesc]       = useState('');
  const [sys, setSys]         = useState('');
  const [ident, setIdent]     = useState('');
  const [logs, setLogs]       = useState('');
  const [payload, setPayload] = useState('');
  const [showAdv, setShowAdv] = useState(false);

  // Estado do upload
  const [uploadFile, setUploadFile]     = useState<UploadedFile | null>(null);
  const [uploadTarget, setUploadTarget] = useState<'logs' | 'payload'>('logs');
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Estado do diagnóstico
  const [loading, setLoading]       = useState(false);
  const [result, setResult]         = useState<DiagnosisResponse | null>(null);
  const [error, setError]           = useState('');
  const [showReport, setShowReport] = useState(false);
  const resultRef = useRef<HTMLDivElement>(null);

  // Submete o formulário ao backend
  async function submit() {
    if (!desc.trim()) {
      setError(t('diagnose.needDescription'));
      return;
    }
    setError('');
    setLoading(true);
    setResult(null);
    setShowReport(false);

    try {
      // Monta o payload — só inclui campos preenchidos
      const body: IncidentRequest = { description: desc.trim() };
      if (sys)           body.interface_type = sys as InterfaceType;
      if (ident.trim())  body.identifier     = ident.trim();
      if (logs.trim())   body.logs           = logs.trim();
      if (payload.trim()) body.payload       = payload.trim();

      const res = await callDiagnose(body);
      setResult(res);
      onResult({ description: desc, interface_type: sys, identifier: ident, result: res, ts: new Date() });
      setTimeout(() => resultRef.current?.scrollIntoView({ behavior: 'smooth' }), 100);
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 401) {
          // DA-54: sessão expirada em pleno uso — o App troca para a
          // tela de login com aviso; aqui só o rastro do motivo
          setError(t('diagnose.sessionExpired401'));
          onSessionExpired();
        } else {
          // Erro HTTP do backend: mensagem por `code` (budget_exhausted,
          // policy_violation, overloaded, timeout) ou por status, com o
          // error_id para achar o log
          setError(describeApiError(e));
        }
      } else {
        // Erro de rede (servidor offline, timeout)
        setError(t('common.networkError'));
      }
    } finally {
      setLoading(false);
    }
  }

  // Lê arquivo e preenche logs ou payload
  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    const ALLOWED = ['.txt', '.log', '.xml', '.json', '.csv', '.md'];
    const ext = '.' + file.name.split('.').pop()!.toLowerCase();
    if (!ALLOWED.includes(ext)) {
      setError(t('diagnose.unsupportedType', { types: ALLOWED.join(', ') }));
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError(t('diagnose.fileTooLarge', { kb: MAX_UPLOAD_BYTES / 1000 }));
      return;
    }

    const reader = new FileReader();
    reader.onload = (ev) => {
      const content = ev.target?.result as string;
      setUploadFile({ name: file.name, content, target: uploadTarget });
      if (uploadTarget === 'logs') setLogs(content);
      else setPayload(content);
      setShowAdv(true);
    };
    reader.readAsText(file);
    e.target.value = '';
  }

  function removeUpload() {
    if (uploadFile?.target === 'logs') setLogs('');
    else setPayload('');
    setUploadFile(null);
  }

  function fillDemo(id: string, s: string, d: string) {
    setIdent(id); setSys(s); setDesc(t(d));
    setResult(null); setError('');
  }

  return (
    <div className="view">
      <h1 className="page-title">{t('diagnose.title')}</h1>
      <p className="page-sub">{t('diagnose.subtitle')}</p>

      <div className="form-card">
        {/* Descrição */}
        <div className="field-row">
          <label className="field-label">{t('diagnose.description')}</label>
          <textarea
            className="field-input"
            rows={4}
            placeholder={t('diagnose.descriptionPlaceholder')}
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
          />
        </div>

        {/* Sistema + Identificador */}
        <div className="grid-2 field-row">
          <div>
            <label className="field-label">{t('diagnose.system')}</label>
            <select
              className="field-input"
              value={sys}
              onChange={(e) => setSys(e.target.value)}
            >
              {SYSTEMS.map(([v, l]) => (
                <option key={v} value={v}>{t(l)}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="field-label">{t('diagnose.identifier')}</label>
            <input
              className="field-input mono-input"
              type="text"
              placeholder={'INC0010001 · CPI-401-DEMO · RFC-IDOC-51' /* i18n-ignore: identificadores */}
              value={ident}
              onChange={(e) => setIdent(e.target.value)}
            />
          </div>
        </div>

        {/* Upload de arquivo */}
        <div className="field-row">
          <label className="field-label">{t('diagnose.attach')}</label>
          <div className="upload-zone" onClick={() => fileInputRef.current?.click()}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M8 2v8M5 5l3-3 3 3" stroke="#0EA5E9"
                strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M2 11v1a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2v-1"
                stroke="#94A3B8" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
            <label className="upload-zone-label">
              <span>{t('diagnose.attachClick')}</span> {t('diagnose.attachDrag')}
              <div style={{ fontSize: 10, color: '#475569', marginTop: 2 }}>
                {t('diagnose.attachHint', { types: '.txt · .log · .xml · .json · .csv', kb: MAX_UPLOAD_BYTES / 1000 })}
              </div>
            </label>
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.log,.xml,.json,.csv,.md"
              onChange={handleFileSelect}
              style={{ display: 'none' }}
            />
          </div>
          {uploadFile && (
            <div style={{ marginTop: 6 }}>
              <span className="upload-chip">
                📎 {uploadFile.name}
                <span style={{ fontSize: 10, color: '#94A3B8', marginLeft: 4 }}>
                  → {uploadFile.target}
                </span>
                <button
                  className="upload-chip-remove"
                  aria-label={t('diagnose.removeAttachment')}
                  title={t('diagnose.removeAttachment')}
                  onClick={(e) => { e.stopPropagation(); removeUpload(); }}
                >
                  ×
                </button>
              </span>
            </div>
          )}
          <div className="upload-target-select">
            <span style={{ fontSize: 11, color: '#475569', alignSelf: 'center' }}>
              {t('diagnose.sendAs')}
            </span>
            {(['logs', 'payload'] as const).map((t) => (
              <button
                key={t}
                className={`upload-target-btn ${uploadTarget === t ? 'active' : ''}`}
                onClick={(e) => { e.stopPropagation(); setUploadTarget(t); }}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* Campos avançados */}
        <button className="advanced-toggle" onClick={() => setShowAdv((v) => !v)}>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path
              d={showAdv ? 'M2 4l4 4 4-4' : 'M4 2l4 4-4 4'}
              stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"
            />
          </svg>
          {showAdv ? t('diagnose.advHide') : t('diagnose.advShow')}
        </button>

        {showAdv && (
          <>
            <div className="field-row">
              <label className="field-label">{t('diagnose.logs')}</label>
              <textarea
                className="field-input mono-input"
                rows={4}
                placeholder={t('diagnose.logsPlaceholder')}
                value={logs}
                onChange={(e) => setLogs(e.target.value)}
              />
            </div>
            <div className="field-row">
              <label className="field-label">{t('diagnose.payload')}</label>
              <textarea
                className="field-input mono-input"
                rows={3}
                placeholder={t('diagnose.payloadPlaceholder')}
                value={payload}
                onChange={(e) => setPayload(e.target.value)}
              />
            </div>
          </>
        )}

        {error && <div className="form-error">{error}</div>}

        <div className="btn-row">
          <button className="submit-btn" onClick={submit} disabled={loading}>
            {loading ? (
              <>
                <svg
                  style={{ width: 14, height: 14, flexShrink: 0,
                    animation: 'spin .9s linear infinite' }}
                  viewBox="0 0 14 14" fill="none"
                >
                  <circle cx="7" cy="7" r="5.5"
                    stroke="rgba(255,255,255,.3)" strokeWidth="2" />
                  <path d="M7 1.5a5.5 5.5 0 0 1 5.5 5.5"
                    stroke="white" strokeWidth="2" strokeLinecap="round" />
                </svg>
                {t('diagnose.analyzing')}
              </>
            ) : (
              <>
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <polygon points="7,1 13,4.5 13,10.5 7,14 1,10.5 1,4.5"
                    stroke="white" strokeWidth="1.2" fill="none" />
                  <circle cx="7" cy="7" r="2" fill="white" />
                </svg>
                {t('diagnose.start')}
              </>
            )}
          </button>
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div className="loading-card">
          <div className="spinner" style={{
            width: 40, height: 40, borderRadius: '50%',
            border: '2px solid #1E3A5F', borderTopColor: '#0EA5E9',
            animation: 'spin .9s linear infinite',
          }} />
          <div className="loading-title">{t('diagnose.processingTitle')}</div>
          <div className="loading-sub">{t('diagnose.processingSub')}</div>
        </div>
      )}

      {/* Resultado */}
      {result && !loading && (
        <div className="result-card" ref={resultRef}>
          <div className="result-header">
            <span className="result-header-title">{t('diagnose.resultTitle')}</span>
            <Badge value={result.diagnosis_confidence} />
          </div>
          <div className="result-body">
            <EscalationBanner signal={result.escalation} />

            <div className="section-label">{t('diagnose.rootCause')}</div>
            <p className="root-cause">{result.probable_root_cause}</p>

            <div className="section-label">{t('diagnose.nextSteps')}</div>
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
                <span className="ref-label">{t('diagnose.referenceDoc')}</span>
                <span className="ref-name">{result.matched_source}</span>
              </div>
            )}

            <EvidenceList evidence={result.evidence} />
            <Provenance result={result} />

            {result.report_markdown && (
              <div className="report-section">
                <button
                  className="report-toggle"
                  onClick={() => setShowReport((v) => !v)}
                >
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                    <path
                      d={showReport ? 'M2 4l4 4 4-4' : 'M4 2l4 4-4 4'}
                      stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"
                    />
                  </svg>
                  {showReport ? t('diagnose.reportHide') : t('diagnose.reportShow')}
                </button>
                {showReport && (
                  // Avaliacao externa (nova revisao, P1 - XSS): antes
                  // disto, "report_markdown" (que pode conter texto
                  // livre digitado pelo usuario - descricao, logs,
                  // payload) era injetado como HTML CRU via
                  // dangerouslySetInnerHTML, sem nenhuma sanitizacao -
                  // um incidente com "<img src=x onerror=...>" na
                  // descricao executava no browser de quem visse o
                  // relatorio. react-markdown NAO interpreta tags HTML
                  // embutidas no texto como HTML de verdade (viram
                  // texto escapado por padrao, sem o plugin
                  // rehype-raw, que deliberadamente NAO usamos aqui) -
                  // so renderiza a sintaxe Markdown real (#, **, -,
                  // etc.) para os elementos HTML correspondentes.
                  <div className="report-body">
                    <ReactMarkdown>{result.report_markdown}</ReactMarkdown>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Demo IDs */}
      <div className="demo-ids">
        <div className="demo-label">{t('diagnose.demoIds')}</div>
        <div className="demo-chips">
          {DEMO_IDS.map((d) => (
            <button
              key={d.id}
              className="demo-chip"
              onClick={() => fillDemo(d.id, d.sys, d.desc)}
            >
              {d.id}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
