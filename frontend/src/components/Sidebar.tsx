// Sidebar — navegação lateral com 3 itens.
//
// CONCEITO NOVO: funções como props
// Em TypeScript, você pode tipar uma função:
//   onClick: () => void        → função sem parâmetros, sem retorno
//   onChange: (v: string) => void → função que recebe string, sem retorno
//   onFetch: () => Promise<Data>  → função assíncrona que retorna Data

type ViewId = 'diagnose' | 'history' | 'status';

interface SidebarProps {
  view: ViewId;                    // qual tela está ativa
  setView: (v: ViewId) => void;    // função que muda a tela
  historyCount: number;            // badge do histórico
}

interface NavItem {
  id: ViewId;
  label: string;
  badge?: number;                  // ? = opcional
  icon: React.ReactNode;           // qualquer JSX válido
}

export function Sidebar({ view, setView, historyCount }: SidebarProps) {
  const items: NavItem[] = [
    {
      id: 'diagnose',
      label: 'Diagnóstico',
      icon: (
        <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
          <polygon points="7.5,1.5 13,5 13,11 7.5,14 2,11 2,5"
            stroke="currentColor" strokeWidth="1.2" />
          <circle cx="7.5" cy="7.5" r="2"
            stroke="currentColor" strokeWidth="1.2" />
        </svg>
      ),
    },
    {
      id: 'history',
      label: 'Histórico',
      badge: historyCount,
      icon: (
        <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
          <rect x="2" y="2" width="11" height="11" rx="2"
            stroke="currentColor" strokeWidth="1.2" />
          <path d="M5 5h5M5 7.5h5M5 10h3"
            stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
        </svg>
      ),
    },
    {
      id: 'status',
      label: 'Stack',
      icon: (
        <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
          <circle cx="7.5" cy="7.5" r="5.5"
            stroke="currentColor" strokeWidth="1.2" />
          <path d="M7.5 4v3.5l2 2"
            stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
        </svg>
      ),
    },
  ];

  return (
    <aside className="sidebar">
      {/* Brand */}
      <div className="brand">
        <div className="brand-icon">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <polygon points="7,1 13,4.5 13,10.5 7,14 1,10.5 1,4.5"
              stroke="white" strokeWidth="1.2" fill="none" />
            <circle cx="7" cy="7" r="2" fill="white" />
          </svg>
        </div>
        <div>
          <div className="brand-name">Incident Copilot</div>
          <div className="brand-sub">SAP Integration · AI</div>
        </div>
      </div>

      {/* Navegação */}
      <nav>
        {items.map((item) => (
          <button
            key={item.id}
            className={`nav-btn ${view === item.id ? 'active' : ''}`}
            onClick={() => setView(item.id)}
          >
            {item.icon}
            {item.label}
            {/* Exibe badge só quando historyCount > 0 */}
            {item.badge != null && item.badge > 0 && (
              <span className="nav-badge">{item.badge}</span>
            )}
          </button>
        ))}
      </nav>

      {/* Modelo ativo */}
      <div className="model-info">
        <div className="model-label">modelo ativo</div>
        <div className="model-name">qwen3-coder-next</div>
        <div className="model-meta">80B/3B · 262K ctx</div>
      </div>
    </aside>
  );
}
