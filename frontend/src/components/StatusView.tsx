// StatusView — estado dos conectores e infraestrutura.
//
// CONCEITO: tipos literais em arrays de objetos
// status: 'ok' | 'warn' | 'mock' — só esses três valores são válidos

type ConnectorStatus = 'ok' | 'warn' | 'mock';

interface ConnectorRow {
  name: string;
  status: ConnectorStatus;
  note: string;
}

interface InfraRow {
  name: string;
  value: string;
  status: ConnectorStatus;
}

const CONNECTORS: ConnectorRow[] = [
  { name: 'ODataConnector',          status: 'mock', note: 'ODATA_SERVICE_URL não configurado' },
  { name: 'RFCConnector',            status: 'ok',   note: 'SDK 7.50 PL19 + pyrfc 3.3.1 · ABAP Trial A4H rel 754' },
  { name: 'ServiceNowConnector',     status: 'ok',   note: 'Validado — PDI real' },
  { name: 'SalesforceConnector',     status: 'ok',   note: 'Validado — Developer Edition' },
  { name: 'WorkdayConnector',        status: 'mock', note: 'Sem sandbox gratuito disponível' },
  { name: 'AribaConnector',          status: 'mock', note: 'Sandbox incompatível com OAuth2 CC' },
  { name: 'CAPConnector',            status: 'ok',   note: 'Validado — BTP Trial (HANA Cloud + XSUAA)' },
  { name: 'APIManagementConnector',  status: 'warn', note: 'Schema especulativo · não validado' },
];

const INFRA: InfraRow[] = [
  { name: 'LLM Gateway',      value: 'qwen3-coder-next:latest · 80B/3B · 262K ctx', status: 'ok' },
  { name: 'RAG (Qdrant)',      value: 'dense + sparse BM25 · fusão RRF · reranker cross-encoder', status: 'ok' },
  { name: 'GraphRAG',          value: 'Neo4j opt-in · desligado por padrão', status: 'mock' },
  { name: 'Observabilidade',   value: 'Langfuse · trace por execução', status: 'ok' },
  { name: 'A2A',               value: 'JSON-RPC 2.0 · aguardando Joule GA Q4/2026', status: 'warn' },
];

function dotClass(s: ConnectorStatus): string {
  return s === 'ok' ? 'status-dot-ok' : s === 'warn' ? 'status-dot-warn' : 'status-dot-mock';
}

function tagColor(s: ConnectorStatus): string {
  return s === 'ok' ? '#10B981' : s === 'warn' ? '#F59E0B' : '#475569';
}

export function StatusView() {
  return (
    <div className="view">
      <h1 className="page-title">Status da stack</h1>
      <p className="page-sub">Estado atual dos conectores e serviços de infraestrutura.</p>

      <div className="status-section">
        <div className="status-section-label">Conectores (8)</div>
        {CONNECTORS.map((c) => (
          <div key={c.name} className="status-row">
            <span className={`status-dot ${dotClass(c.status)}`} />
            <span className="status-name" style={{ width: 210 }}>{c.name}</span>
            <span className="status-note">{c.note}</span>
            <span className="status-tag" style={{ color: tagColor(c.status) }}>
              {c.status}
            </span>
          </div>
        ))}
      </div>

      <div className="status-section">
        <div className="status-section-label">Infraestrutura</div>
        {INFRA.map((s) => (
          <div key={s.name} className="status-row">
            <span className={`status-dot ${dotClass(s.status)}`} />
            <span className="status-name" style={{ width: 150 }}>{s.name}</span>
            <span className="status-note" style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
            }}>
              {s.value}
            </span>
            <span className="status-tag" style={{ color: tagColor(s.status) }}>
              {s.status}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
