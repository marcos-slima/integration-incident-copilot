// StatusView — estado dos conectores e infraestrutura.
//
// Avaliacao externa (qualidade, item 27): esta view renderizava arrays
// hardcoded (nomes de SDK, "Validado — PDI real" etc.) que nao refletiam
// o backend real e ficavam desatualizados a cada mudanca de config. Agora
// busca o estado de verdade em GET /health (app.connectors.connector_status,
// derivado do .env atual do backend) ao montar o componente.

import { useEffect, useState } from 'react';
import { callHealth } from '../api/health';
import { ApiError } from '../api/diagnose';
import type { ConnectorHealthStatus, HealthResponse, InterfaceType } from '../types/models';

// Nome de exibicao de cada conector - so rotulagem, o estado vem do backend
const CONNECTOR_LABELS: Record<InterfaceType, string> = {
  odata: 'ODataConnector',
  rfc: 'RFCConnector',
  servicenow: 'ServiceNowConnector',
  salesforce: 'SalesforceConnector',
  workday: 'WorkdayConnector',
  ariba: 'AribaConnector',
  cap: 'CAPConnector',
  apim: 'APIManagementConnector',
};

// Ordem de exibicao (a mesma do dropdown de conectores em DiagnoseView)
const CONNECTOR_ORDER: InterfaceType[] = [
  'odata',
  'rfc',
  'servicenow',
  'salesforce',
  'workday',
  'ariba',
  'cap',
  'apim',
];

function dotClass(s: ConnectorHealthStatus): string {
  if (s === 'real') return 'status-dot-ok';
  if (s === 'misconfigured') return 'status-dot-warn';
  return 'status-dot-mock';
}

function tagColor(s: ConnectorHealthStatus): string {
  if (s === 'real') return '#10B981';
  if (s === 'misconfigured') return '#F59E0B';
  return '#475569';
}

function boolDotClass(v: boolean): string {
  return v ? 'status-dot-ok' : 'status-dot-mock';
}

function boolTagColor(v: boolean): string {
  return v ? '#10B981' : '#475569';
}

export function StatusView() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    callHealth()
      .then((res) => {
        if (!cancelled) setHealth(res);
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof ApiError) setError(`Erro ${e.status}: ${e.message}`);
        else setError('Falha ao consultar o backend. Verifique se a API está ativa.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="view">
      <h1 className="page-title">Status da stack</h1>
      <p className="page-sub">
        Estado atual dos conectores e serviços de infraestrutura, consultado ao vivo em
        GET /health.
      </p>

      {loading && <p className="page-sub">Consultando /health…</p>}
      {error && <p className="page-sub" style={{ color: '#EF4444' }}>{error}</p>}

      {health && (
        <>
          <div className="status-section">
            <div className="status-section-label">
              Conectores ({CONNECTOR_ORDER.length})
            </div>
            {CONNECTOR_ORDER.map((name) => {
              const info = health.connectors[name];
              return (
                <div key={name} className="status-row">
                  <span className={`status-dot ${dotClass(info.status)}`} />
                  <span className="status-name" style={{ width: 210 }}>
                    {CONNECTOR_LABELS[name]}
                  </span>
                  <span className="status-note">{info.note}</span>
                  <span className="status-tag" style={{ color: tagColor(info.status) }}>
                    {info.status}
                  </span>
                </div>
              );
            })}
          </div>

          <div className="status-section">
            <div className="status-section-label">Infraestrutura</div>
            <div className="status-row">
              <span className="status-dot status-dot-ok" />
              <span className="status-name" style={{ width: 150 }}>
                LLM Provider
              </span>
              <span
                className="status-note"
                style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}
              >
                {health.infra.llm_provider}
              </span>
            </div>
            <div className="status-row">
              <span className={`status-dot ${boolDotClass(health.infra.graph_rag_enabled)}`} />
              <span className="status-name" style={{ width: 150 }}>
                GraphRAG (Neo4j)
              </span>
              <span
                className="status-note"
                style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}
              >
                {health.infra.graph_rag_enabled ? 'ligado' : 'desligado (opt-in)'}
              </span>
              <span
                className="status-tag"
                style={{ color: boolTagColor(health.infra.graph_rag_enabled) }}
              >
                {health.infra.graph_rag_enabled ? 'on' : 'off'}
              </span>
            </div>
            <div className="status-row">
              <span className={`status-dot ${boolDotClass(health.infra.langfuse_enabled)}`} />
              <span className="status-name" style={{ width: 150 }}>
                Observabilidade (Langfuse)
              </span>
              <span
                className="status-note"
                style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}
              >
                {health.infra.langfuse_enabled ? 'credenciais configuradas' : 'sem credenciais no .env'}
              </span>
              <span
                className="status-tag"
                style={{ color: boolTagColor(health.infra.langfuse_enabled) }}
              >
                {health.infra.langfuse_enabled ? 'on' : 'off'}
              </span>
            </div>
            <div className="status-row">
              <span className={`status-dot ${boolDotClass(health.infra.async_queue_enabled)}`} />
              <span className="status-name" style={{ width: 150 }}>
                Fila assíncrona (RQ)
              </span>
              <span
                className="status-note"
                style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}
              >
                {health.infra.async_queue_enabled
                  ? 'REDIS_URL configurada'
                  : '/diagnose/async indisponível (sem REDIS_URL)'}
              </span>
              <span
                className="status-tag"
                style={{ color: boolTagColor(health.infra.async_queue_enabled) }}
              >
                {health.infra.async_queue_enabled ? 'on' : 'off'}
              </span>
            </div>
            <div className="status-row">
              <span className={`status-dot ${boolDotClass(health.infra.auth_required)}`} />
              <span className="status-name" style={{ width: 150 }}>
                Autenticação (X-API-Key)
              </span>
              <span
                className="status-note"
                style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}
              >
                {health.infra.auth_required ? 'exigida em /diagnose e /a2a' : 'desligada'}
              </span>
              <span
                className="status-tag"
                style={{ color: boolTagColor(health.infra.auth_required) }}
              >
                {health.infra.auth_required ? 'on' : 'off'}
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
