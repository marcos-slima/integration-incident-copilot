// DF-e Copilot - shell de produto (DA-95). Uma tela: entrada fiscal e
// resultado. Sessao, login e idioma vem de @copilot/core-ui; o produto nao
// tem navegacao, conectores nem historico do IIC.

import { useTranslation } from 'react-i18next';
import { LanguageSelect, SessionGate } from '@copilot/core-ui';
import { FiscalDiagnoseView } from './components/FiscalDiagnoseView';

function App() {
  const { t } = useTranslation();
  const brand = { name: t('common.appName'), sub: t('common.appSub') };
  return (
    <SessionGate brand={brand}>
      {({ session, logout, expire }) => (
        <div className="dfe-app">
          <header className="dfe-topbar">
            <div className="brand dfe-brand">
              <div className="brand-icon dfe-brand-icon" aria-hidden="true">
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <rect x="2.5" y="1.5" width="9" height="11" rx="1.5" stroke="white" strokeWidth="1.2" />
                  <path d="M4.5 5h5M4.5 7.5h5M4.5 10h3" stroke="white" strokeWidth="1.2" strokeLinecap="round" />
                </svg>
              </div>
              <div>
                <div className="brand-name">{brand.name}</div>
                <div className="brand-sub">{brand.sub}</div>
              </div>
            </div>
            <div className="dfe-topbar-right">
              <LanguageSelect compact />
              <span className="session-user">{session.username}</span>
              <button className="session-logout" onClick={logout}>{t('common.logout')}</button>
            </div>
          </header>
          <main className="main">
            <FiscalDiagnoseView onSessionExpired={expire} />
          </main>
        </div>
      )}
    </SessionGate>
  );
}

export default App;
