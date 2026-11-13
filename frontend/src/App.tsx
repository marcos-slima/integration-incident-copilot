// App do Integration Incident Copilot (shell de produto, DA-95).
//
// O portao de sessao, o login e o idioma vem de @copilot/core-ui; aqui fica
// so o que e' do IIC: navegacao, historico da sessao e as telas de produto.
// O historico vive no App (ancestral comum de DiagnoseView e HistoryView).

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { SessionGate } from '@copilot/core-ui';
import { DiagnoseView } from './components/DiagnoseView';
import { HistoryView } from './components/HistoryView';
import { Sidebar } from './components/Sidebar';
import { StatusView } from './components/StatusView';
import type { HistoryItem } from './types/models';

type ViewId = 'diagnose' | 'history' | 'status';

function App() {
  const { t } = useTranslation();
  const [view, setView] = useState<ViewId>('diagnose');
  const [history, setHistory] = useState<HistoryItem[]>([]);

  return (
    <SessionGate brand={{ name: t('common.appName'), sub: t('common.appSub') }}>
      {({ session, logout, expire }) => (
        <div className="app">
          <Sidebar
            view={view}
            setView={setView}
            historyCount={history.length}
            username={session.username}
            onLogout={() => {
              logout();
              setView('diagnose');
            }}
            lastResult={history.at(-1)?.result}
          />
          <main className="main">
            {view === 'diagnose' && (
              <DiagnoseView onResult={(item) => setHistory((prev) => [...prev, item])} onSessionExpired={expire} />
            )}
            {view === 'history' && <HistoryView history={history} />}
            {view === 'status' && <StatusView />}
          </main>
        </div>
      )}
    </SessionGate>
  );
}

export default App;
