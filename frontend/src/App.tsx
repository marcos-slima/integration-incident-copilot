// App — componente raiz que gerencia navegação e histórico.
//
// CONCEITO: lifting state up
// O histórico precisa ser acessado por DiagnoseView (para adicionar)
// e por HistoryView (para exibir). A solução em React é guardar o estado
// no ancestral comum — o App — e passar via props para os filhos.

import { useState } from 'react';
import { DiagnoseView } from './components/DiagnoseView';
import { HistoryView } from './components/HistoryView';
import { Sidebar } from './components/Sidebar';
import { StatusView } from './components/StatusView';
import type { HistoryItem } from './types/models';

// ViewId é o mesmo type definido no Sidebar
type ViewId = 'diagnose' | 'history' | 'status';

function App() {
  const [view, setView]       = useState<ViewId>('diagnose');
  const [history, setHistory] = useState<HistoryItem[]>([]);

  function addToHistory(item: HistoryItem) {
    setHistory((prev) => [...prev, item]);
  }

  return (
    <div className="app">
      <Sidebar
        view={view}
        setView={setView}
        historyCount={history.length}
      />
      <main className="main">
        {view === 'diagnose' && (
          <DiagnoseView onResult={addToHistory} />
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
