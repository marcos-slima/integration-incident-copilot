// LoginView — tela de login independente (DA-54).
//
// Por que uma TELA, e não um campo no formulário de diagnóstico:
// - usabilidade: quem chega para diagnosticar um incidente encontra um
//   formulário limpo — não um bloco de credencial misturado com o caso;
// - segurança: quem não tem sessão não vê NADA do app — nem o formulário,
//   nem o histórico, nem o estado da stack. A tela de login é a única
//   superfície antes da sessão existir.
// A sessão vence? O app volta para cá com aviso — sem estado parcial.

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { login, verifyEmail, verifyPhone, type SessionState } from '../api/auth';
import { ApiError } from '../api/http';
import { LanguageSelect } from './LanguageSelect';

export interface ProductBrand {
  name: string; // nome do produto (texto ja traduzido pelo shell)
  sub: string;
}

interface LoginViewProps {
  brand: ProductBrand; // DA-95: identidade vem do shell do produto
  onSuccess: (session: SessionState) => void;
  notice?: string; // ex.: aviso de sessao expirada (ja traduzido pelo shell)
}

// Ativacao de conta (DA-55) em duas etapas: token de e-mail -> codigo de
// telefone. Quem ativou entra pelo login normal.
type Mode = 'login' | 'activate';

export function LoginView({ brand, onSuccess, notice }: LoginViewProps) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<Mode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);

  // ativacao
  const [token, setToken] = useState('');
  const [code, setCode] = useState('');
  const [step, setStep] = useState<'email' | 'phone'>('email');
  const [okMsg, setOkMsg] = useState('');

  async function submit() {
    if (pending) return;
    if (!username.trim() || !password) {
      setError(t('core.login.fillUserPassword'));
      return;
    }
    setPending(true);
    setError('');
    try {
      const s = await login(username.trim(), password);
      onSuccess(s);
    } catch (e) {
      if (e instanceof ApiError) {
        // 401 = usuário/senha inválidos (o servidor não revela qual);
        // 429 = rate limit de login (5/min — aqui se testa senha)
        setError(t('core.common.errorStatus', { status: e.status, message: e.message }));
      } else {
        setError(t('core.common.networkError'));
      }
    } finally {
      setPending(false);
    }
  }

  async function submitEmailToken() {
    if (pending) return;
    setPending(true);
    setError('');
    try {
      await verifyEmail(username.trim(), token.trim());
      setStep('phone');
      setOkMsg(t('core.login.emailConfirmed'));
      setToken('');
    } catch (e) {
      setError(e instanceof ApiError ? t('core.common.errorStatus', { status: e.status, message: e.message }) : t('core.common.networkErrorShort'));
    } finally {
      setPending(false);
    }
  }

  async function submitPhoneCode() {
    if (pending) return;
    setPending(true);
    setError('');
    try {
      await verifyPhone(username.trim(), code.trim());
      setOkMsg(t('core.login.accountActivated'));
      setCode('');
      setMode('login');
      setPassword('');
    } catch (e) {
      setError(e instanceof ApiError ? t('core.common.errorStatus', { status: e.status, message: e.message }) : t('core.common.networkErrorShort'));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        {/* Brand */}
        <div className="brand" style={{ padding: 0, marginBottom: 18 }}>
          <div className="brand-icon">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <polygon points="7,1 13,4.5 13,10.5 7,14 1,10.5 1,4.5"
                stroke="white" strokeWidth="1.2" fill="none" />
              <circle cx="7" cy="7" r="2" fill="white" />
            </svg>
          </div>
          <div>
            <div className="brand-name">{brand.name}</div>
            <div className="brand-sub">{brand.sub}</div>
          </div>
        </div>

        <div className="login-title">{mode === 'login' ? t('core.login.titleLogin') : t('core.login.titleActivate')}</div>
        <div className="login-sub">
          {mode === 'login' ? t('core.login.subLogin') : t('core.login.subActivate')}
        </div>

        {notice && <div className="login-notice">{notice}</div>}
        {okMsg && <div className="login-ok">{okMsg}</div>}

        <div className="field-row">
          <label className="field-label">{t('core.login.username')}</label>
          <input
            className="field-input mono-input"
            type="text"
            autoComplete="username"
            autoFocus
            placeholder={t('core.login.usernamePlaceholder')}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && (mode === 'login' ? submit() : step === 'email' ? submitEmailToken() : submitPhoneCode())}
          />
        </div>

        {mode === 'login' && (
          <div className="field-row">
            <label className="field-label">{t('core.login.password')}</label>
            <input
              className="field-input mono-input"
              type="password"
              autoComplete="current-password"
              placeholder={t('core.login.passwordPlaceholder')}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submit()}
            />
          </div>
        )}

        {mode === 'activate' && step === 'email' && (
          <div className="field-row">
            <label className="field-label">{t('core.login.emailToken')}</label>
            <input
              className="field-input mono-input"
              type="text"
              placeholder={t('core.login.emailTokenPlaceholder')}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submitEmailToken()}
            />
          </div>
        )}

        {mode === 'activate' && step === 'phone' && (
          <div className="field-row">
            <label className="field-label">{t('core.login.phoneCode')}</label>
            <input
              className="field-input mono-input"
              type="text"
              inputMode="numeric"
              maxLength={6}
              placeholder={t('core.login.phoneCodePlaceholder')}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submitPhoneCode()}
            />
          </div>
        )}

        {error && <div className="form-error">{error}</div>}

        {mode === 'login' && (
          <button className="submit-btn login-submit" onClick={submit} disabled={pending}>
            {pending ? t('core.login.submitting') : t('core.login.submit')}
          </button>
        )}
        {mode === 'activate' && (
          <button
            className="submit-btn login-submit"
            onClick={step === 'email' ? submitEmailToken : submitPhoneCode}
            disabled={pending}
          >
            {pending ? t('core.login.confirming') : step === 'email' ? t('core.login.confirmEmail') : t('core.login.confirmPhone')}
          </button>
        )}

        <button
          className="login-alt"
          onClick={() => {
            setMode(mode === 'login' ? 'activate' : 'login');
            setStep('email');
            setError('');
            setOkMsg('');
          }}
        >
          {mode === 'login' ? t('core.login.toActivate') : t('core.login.backToLogin')}
        </button>

        <div className="login-foot">{t('core.login.foot')}</div>
        <LanguageSelect compact />
      </div>
    </div>
  );
}
