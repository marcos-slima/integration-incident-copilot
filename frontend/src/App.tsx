// App — componente raiz que gerencia navegação, histórico e sessão.
//
// CONCEITO: lifting state up
// O histórico precisa ser acessado por DiagnoseView (para adicionar)
// e por HistoryView (para exibir). A solução em React é guardar o estado
// no ancestral comum — o App — e passar via props para os filhos.
//
// DA-54: a sessão também vive aqui, e é PORTÃO: sem sessão, o app inteiro
// não renderiza — só a LoginView. `session === null` significa "ainda
// checando" (evita piscar a tela de login para quem já tem cookie
// válido); a checagem é o GET /auth/session.

import { useEffect, useState } from 'react';
import { DiagnoseView } from './components/DiagnoseView';
import { HistoryView } from './components/HistoryView';
import { LoginView } from './components/LoginView';
import { Sidebar } from './components/Sidebar';
import { StatusView } from './components/StatusView';
import { getSession, logout, type SessionState } from './api/auth';
import type { HistoryItem } from './types/models';

// ViewId é o mesmo type definido no Sidebar
type ViewId = 'diagnose' | 'history' | 'status';

function App() {
  const [view, setView]       = useState<ViewId>('diagnose');
  const [history, setHistory] = useState<HistoryItem[]>([]);
  // null = checando sessão; {authenticated:false} = sem sessão (login)
  const [session, setSession] = useState<SessionState | null>(null);
  // mensagem para a tela de login quando a sessão expira em pleno uso
  const [notice, setNotice]   = useState('');

  useEffect(() => {
    getSession()
      .then(setSession)
      .catch(() => setSession({ authenticated: false }));
  }, []);

  function addToHistory(item: HistoryItem) {
    setHistory((prev) => [...prev, item]);
  }

  function handleLoginSuccess(s: SessionState) {
    setSession(s);
    setNotice('');
    setView('diagnose');
  }

  function handleLogout() {
    logout().catch(() => {});
    setSession({ authenticated: false });
    setNotice('');
  }

  function handleSessionExpired() {
    setSession({ authenticated: false });
    setNotice('Sua sessão expirou — faça login novamente.');
  }

  // Checando sessão: loading mínimo, sem flash da tela de login
  if (session === null) {
    return (
      <div className="login-wrap">
        <div className="login-card" style={{ alignItems: 'center' }}>
          <div className="spinner" style={{
            width: 32, height: 32, borderRadius: '50%',
            border: '2px solid #1E3A5F', borderTopColor: '#0EA5E9',
            animation: 'spin .9s linear infinite',
          }} />
          <div style={{ marginTop: 12, fontSize: 12, color: '#94A3B8' }}>
            Verificando sessão…
          </div>
        </div>
      </div>
    );
  }

  // Sem sessão: SÓ a tela de login — nada do app renderiza antes dela
  if (!session.authenticated) {
    return <LoginView onSuccess={handleLoginSuccess} notice={notice} />;
  }

  return (
    <div className="app">
      <Sidebar
        view={view}
        setView={setView}
        historyCount={history.length}
        username={session.username}
        onLogout={handleLogout}
      />
      <main className="main">
        {view === 'diagnose' && (
          <DiagnoseView onResult={addToHistory} onSessionExpired={handleSessionExpired} />
        )}
        {view === 'history' && (
          <HistoryView history={history} />
        )}
        {view === 'status' && (
          <StatusView />
        )}
      </main>
    </div>
  );
}

export default App;
