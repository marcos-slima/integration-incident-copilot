// HistoryView — lista de diagnósticos da sessão.
//
// CONCEITO: props com array tipado
// history: HistoryItem[] significa "array de HistoryItem"

import type { HistoryItem } from '../types/models';
import { Badge } from './Badge';

interface HistoryViewProps {
  history: HistoryItem[];
}

export function HistoryView({ history }: HistoryViewProps) {
  if (!history.length) {
    return (
      <div className="view">
        <h1 className="page-title">Histórico da sessão</h1>
        <div className="history-empty">
          <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
            <rect x="4" y="4" width="24" height="24" rx="4"
              stroke="currentColor" strokeWidth="1.5" />
            <path d="M10 11h12M10 16h12M10 21h7"
              stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
          <p style={{ fontSize: 13 }}>Nenhum diagnóstico nesta sessão.</p>
          <p style={{ fontSize: 12, marginTop: 4 }}>
            Os resultados aparecem aqui após o primeiro diagnóstico.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="view">
      <h1 className="page-title">Histórico da sessão</h1>
      <p className="page-sub" style={{ marginBottom: 20 }}>
        Diagnósticos realizados nesta sessão.
      </p>
      {[...history].reverse().map((item, i) => (
        <div key={i} className="history-item">
          <div className="history-row">
            <p className="history-desc">{item.description}</p>
            <Badge value={item.result.confidence} />
          </div>
          <div className="history-meta">
            {item.interface_type && (
              <span className="sys-tag">{item.interface_type}</span>
            )}
            {item.identifier && (
              <span className="hist-id">{item.identifier}</span>
            )}
            <span className="hist-time">
              {item.ts.toLocaleTimeString('pt-BR')}
            </span>
          </div>
          <p className="history-cause">{item.result.probable_root_cause}</p>
        </div>
      ))}
    </div>
  );
}
