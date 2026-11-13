// SessionGate (DA-54 + DA-95): portao de sessao de qualquer shell.
// Sem sessao, NADA do produto renderiza - so a LoginView. `null` = ainda
// checando (evita piscar o login para quem ja tem cookie valido).

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { getSession, logout, type SessionState } from '../api/auth';
import { LoginView, type ProductBrand } from './LoginView';

export interface SessionControls {
  session: SessionState;
  logout: () => void;
  /** Chame num 401: volta ao login com o aviso de sessao expirada. */
  expire: () => void;
}

interface SessionGateProps {
  brand: ProductBrand;
  children: (controls: SessionControls) => ReactNode;
}

export function SessionGate({ brand, children }: SessionGateProps) {
  const { t } = useTranslation();
  const [session, setSession] = useState<SessionState | null>(null);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    getSession()
      .then(setSession)
      .catch(() => setSession({ authenticated: false }));
  }, []);

  // Controles estaveis: um efeito do produto que dependa de `expire` nao
  // dispara de novo a cada render do portao.
  const doLogout = useCallback(() => {
    logout().catch(() => {});
    setNotice('');
    setSession({ authenticated: false });
  }, []);
  const expire = useCallback(() => {
    setNotice(t('core.common.sessionExpired'));
    setSession({ authenticated: false });
  }, [t]);

  if (session === null) {
    return (
      <div className="login-wrap">
        <div className="login-card" style={{ alignItems: 'center' }}>
          <div className="spinner spinner-sm" />
          <div className="checking-text">{t('core.common.checkingSession')}</div>
        </div>
      </div>
    );
  }

  if (!session.authenticated) {
    return (
      <LoginView
        brand={brand}
        notice={notice}
        onSuccess={(s) => {
          setSession(s);
          setNotice('');
        }}
      />
    );
  }

  return (
    <>
      {children({ session, logout: doLogout, expire })}
    </>
  );
}
