// StatusView — estado dos conectores e infraestrutura.
//
// Avaliacao externa (qualidade, item 27): esta view renderizava arrays
// hardcoded (nomes de SDK, "Validado — PDI real" etc.) que nao refletiam
// o backend real e ficavam desatualizados a cada mudanca de config. Agora
// busca o estado de verdade em GET /health (app.connectors.connector_status,
// derivado do .env atual do backend) ao montar o componente.

import { useEffect, useState } from 'react';
import { callHealth, callLlmPolicy } from '../api/health';
import { ApiError } from '../api/diagnose';
import type { ConnectorHealthStatus, HealthResponse, InterfaceType, LlmPolicy } from '../types/models';

// Nome de exibicao de cada conector - so rotulagem, o estado vem do backend
const CONNECTOR_LABELS: Record<InterfaceType, string> = {
  odata: 'ODataConnector',
  rfc: 'RFCConnector',
  servicenow: 'ServiceNowConnector',
  salesforce: 'SalesforceConnector',
  workday: 'WorkdayConnector',
  ariba: 'AribaConnector',
  successfactors: 'SuccessFactorsConnector',
  cap: 'CAPConnector',
  apim: 'APIManagementConnector',
  po: 'POConnector',
};

// Ordem de exibicao (a mesma do dropdown de conectores em DiagnoseView)
const CONNECTOR_ORDER: InterfaceType[] = [
  'odata',
  'rfc',
  'servicenow',
  'salesforce',
  'workday',
  'ariba',
  'successfactors',
  'cap',
  'apim',
  'po',
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
  const [policy, setPolicy] = useState<LlmPolicy | null>(null);
  const [policyError, setPolicyError] = useState('');

  useEffect(() => {
    let cancelled = false;

    // DA-43/DA-66: independente do /health - uma falha aqui nao esconde o resto
    callLlmPolicy()
      .then((p) => {
        if (!cancelled) setPolicy(p);
      })
      .catch((e) => {
        if (cancelled) return;
        setPolicyError(
          e instanceof ApiError ? `Erro ${e.status}: ${e.message}` : 'Falha ao consultar /llm/policy.',
        );
      });

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
              Conectores ({Object.keys(health.connectors).length})
            </div>
            {/* FE-01: a lista vem do /health; CONNECTOR_ORDER so define a ordem.
                Conector novo no backend aparece mesmo sem rotulo aqui. */}
            {[
              ...CONNECTOR_ORDER.filter((n) => n in health.connectors),
              ...(Object.keys(health.connectors) as InterfaceType[]).filter(
                (n) => !CONNECTOR_ORDER.includes(n),
              ),
            ].map((name) => {
              const info = health.connectors[name];
              return (
                <div key={name} className="status-row">
                  <span className={`status-dot ${dotClass(info.status)}`} />
                  <span className="status-name" style={{ width: 210 }}>
                    {CONNECTOR_LABELS[name] ?? name}
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

      <div className="status-section">
        <div className="status-section-label">Soberania do dado (GET /llm/policy)</div>
        {policyError && <p className="page-sub" style={{ color: '#EF4444' }}>{policyError}</p>}
        {policy && (
          <>
            <div className="status-row">
              <span className="status-dot status-dot-ok" />
              <span className="status-name" style={{ width: 150 }}>Modo</span>
              <span className="status-note">
                {policy.data_sovereignty_mode} · sensibilidade padrão {policy.sensitivity_default}
                {' · '}teto por chamada US$ {policy.limits.max_cost_usd.toFixed(2)}
              </span>
            </div>
            {Object.entries(policy.providers).map(([name, p]) => (
              <div key={name} className="status-row">
                <span className={`status-dot ${p.may_receive_confidential ? 'status-dot-ok' : p.may_receive_public ? 'status-dot-warn' : 'status-dot-mock'}`} />
                <span className="status-name" style={{ width: 150 }}>
                  {name}
                  {name === policy.routing.primary ? ' (primário)' : ''}
                  {name === policy.routing.fallback ? ' (fallback)' : ''}
                </span>
                <span
                  className="status-note"
                  style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}
                  title={p.confidential_reason}
                >
                  {p.locality} · {p.origin ?? 'sem origem configurada'}
                </span>
                <span className="status-tag" style={{ color: p.may_receive_confidential ? '#10B981' : '#F59E0B' }}>
                  {p.may_receive_confidential ? 'confidencial ok' : p.may_receive_public ? 'só público' : 'bloqueado'}
                </span>
              </div>
            ))}
            <p className="status-note" style={{ marginTop: 8 }}>
              Dado confidencial pode ir para: {policy.routing.allowed_for_confidential.join(', ') || 'nenhum provedor'}.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
