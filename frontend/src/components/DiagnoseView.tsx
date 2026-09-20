// DiagnoseView — formulário principal de diagnóstico.
//
// CONCEITOS NOVOS:
// - useRef<T>: referência tipada para elementos DOM
// - async function com try/catch tipado
// - useState<T | null>: estado que pode ser null
// - FileReader API com TypeScript

import { useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { callDiagnose, ApiError } from '../api/diagnose';
import type {
  DiagnosisResponse,
  HistoryItem,
  IncidentRequest,
  InterfaceType,
} from '../types/models';
import { Badge } from './Badge';

// Props do componente
interface DiagnoseViewProps {
  onResult: (item: HistoryItem) => void; // callback para adicionar ao histórico
}

// Sistemas disponíveis no dropdown
const SYSTEMS: Array<[string, string]> = [
  ['', 'Sem conector (texto livre)'],
  ['odata', 'OData / SAP Gateway'],
  ['rfc', 'RFC / SAP ABAP'],
  ['servicenow', 'ServiceNow ITSM'],
  ['salesforce', 'Salesforce CRM'],
  ['workday', 'Workday HCM'],
  ['ariba', 'SAP Ariba'],
  ['cap', 'SAP CAP / BTP'],
  ['apim', 'SAP API Management'],
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
const DEMO_IDS = [
  { id: 'CPI-401-DEMO',        sys: 'odata', desc: 'iFlow retornando HTTP 401 ao autenticar via OAuth2' },
  { id: 'RFC-IDOC-51-DEMO',    sys: 'rfc',   desc: 'IDoc travado com status 51' },
  { id: 'RFC-CONN-REFUSED-DEMO', sys: 'rfc', desc: 'SM59 não conecta no sistema de destino' },
];

// Tipo do arquivo anexado
interface UploadedFile {
  name: string;
  content: string;
  target: 'logs' | 'payload';
}

export function DiagnoseView({ onResult }: DiagnoseViewProps) {
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
      setError('Descreva o incidente para iniciar o diagnóstico.');
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
        // Erro HTTP do backend (401, 422, 429, 500)
        setError(`Erro ${e.status}: ${e.message}`);
      } else {
        // Erro de rede (servidor offline, timeout)
        setError('Falha na comunicação com o servidor. Verifique se o backend está ativo.');
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
      setError(`Tipo não suportado. Use: ${ALLOWED.join(', ')}`);
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError(
        `Arquivo muito grande. Limite: ${MAX_UPLOAD_BYTES / 1000}KB (mesmo limite de ` +
          'caracteres aceito pela API para logs/payload).',
      );
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
    setIdent(id); setSys(s); setDesc(d);
    setResult(null); setError('');
  }

  return (
    <div className="view">
      <h1 className="page-title">Diagnóstico de incidente</h1>
      <p className="page-sub">
        Descreva o incidente em linguagem natural. O agente recupera o contexto
        via RAG, analisa com LLM e retorna a causa raiz provável com próximos
        passos concretos.
      </p>

      <div className="form-card">
        {/* Descrição */}
        <div className="field-row">
          <label className="field-label">Descrição do incidente *</label>
          <textarea
            className="field-input"
            rows={4}
            placeholder="Ex: IDoc travado com status 51 — material não encontrado no centro de produção."
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
          />
        </div>

        {/* Sistema + Identificador */}
        <div className="grid-2 field-row">
          <div>
            <label className="field-label">Sistema de origem</label>
            <select
              className="field-input"
              value={sys}
              onChange={(e) => setSys(e.target.value)}
            >
              {SYSTEMS.map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="field-label">Identificador do incidente</label>
            <input
              className="field-input mono-input"
              type="text"
              placeholder="INC0010001 · CPI-401-DEMO · RFC-IDOC-51"
              value={ident}
              onChange={(e) => setIdent(e.target.value)}
            />
          </div>
        </div>

        {/* Upload de arquivo */}
        <div className="field-row">
          <label className="field-label">Anexar arquivo (opcional)</label>
          <div className="upload-zone" onClick={() => fileInputRef.current?.click()}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M8 2v8M5 5l3-3 3 3" stroke="#0EA5E9"
                strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M2 11v1a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2v-1"
                stroke="#94A3B8" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
            <label className="upload-zone-label">
              <span>Clique para anexar</span> ou arraste um arquivo
              <div style={{ fontSize: 10, color: '#475569', marginTop: 2 }}>
                .txt · .log · .xml · .json · .csv (máx. {MAX_UPLOAD_BYTES / 1000}KB)
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
                  onClick={(e) => { e.stopPropagation(); removeUpload(); }}
                >
                  ×
                </button>
              </span>
            </div>
          )}
          <div className="upload-target-select">
            <span style={{ fontSize: 11, color: '#475569', alignSelf: 'center' }}>
              Enviar como:
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
          {showAdv ? 'Ocultar campos avançados' : 'Campos avançados (logs, payload)'}
        </button>

        {showAdv && (
          <>
            <div className="field-row">
              <label className="field-label">Logs do sistema (opcional)</label>
              <textarea
                className="field-input mono-input"
                rows={4}
                placeholder="Cole aqui os logs relevantes do iFlow, SM21, ST22, etc."
                value={logs}
                onChange={(e) => setLogs(e.target.value)}
              />
            </div>
            <div className="field-row">
              <label className="field-label">Payload / mensagem de erro (opcional)</label>
              <textarea
                className="field-input mono-input"
                rows={3}
                placeholder="Payload XML, JSON de erro, mensagem de exceção ABAP, etc."
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
                Analisando…
              </>
            ) : (
              <>
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <polygon points="7,1 13,4.5 13,10.5 7,14 1,10.5 1,4.5"
                    stroke="white" strokeWidth="1.2" fill="none" />
                  <circle cx="7" cy="7" r="2" fill="white" />
                </svg>
                Iniciar diagnóstico
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
          <div className="loading-title">Agente processando</div>
          <div className="loading-sub">Conector → RAG híbrido → LLM → guardrails</div>
        </div>
      )}

      {/* Resultado */}
      {result && !loading && (
        <div className="result-card" ref={resultRef}>
          <div className="result-header">
            <span className="result-header-title">Resultado do diagnóstico</span>
            <Badge value={result.confidence} />
          </div>
          <div className="result-body">
            <div className="section-label">Causa raiz provável</div>
            <p className="root-cause">{result.probable_root_cause}</p>

            <div className="section-label">Próximos passos</div>
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
                <span className="ref-label">Documento de referência</span>
                <span className="ref-name">{result.matched_source}</span>
              </div>
            )}

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
                  {showReport ? 'Ocultar relatório completo' : 'Ver relatório completo'}
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
        <div className="demo-label">Identificadores de demonstração</div>
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
