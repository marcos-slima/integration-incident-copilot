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
import { login, type SessionState } from '../api/auth';
import { ApiError } from '../api/diagnose';

interface LoginViewProps {
  onSuccess: (session: SessionState) => void;
  notice?: string; // ex.: "Sua sessão expirou — faça login novamente."
}

export function LoginView({ onSuccess, notice }: LoginViewProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);

  async function submit() {
    if (pending) return;
    if (!username.trim() || !password) {
      setError('Preencha usuário e senha.');
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
        setError(`Erro ${e.status}: ${e.message}`);
      } else {
        setError('Falha na comunicação com o servidor. Verifique se o backend está ativo.');
      }
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
            <div className="brand-name">Incident Copilot</div>
            <div className="brand-sub">SAP Integration · AI</div>
          </div>
        </div>

        <div className="login-title">Entrar</div>
        <div className="login-sub">
          Autenticação de usuário — a sessão é um cookie HttpOnly assinado,
          válido por horas, enviado sozinho pelo navegador.
        </div>

        {notice && <div className="login-notice">{notice}</div>}

        <div className="field-row">
          <label className="field-label">Usuário</label>
          <input
            className="field-input mono-input"
            type="text"
            autoComplete="username"
            autoFocus
            placeholder="seu usuário"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
          />
        </div>
        <div className="field-row">
          <label className="field-label">Senha</label>
          <input
            className="field-input mono-input"
            type="password"
            autoComplete="current-password"
            placeholder="sua senha"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
          />
        </div>

        {error && <div className="form-error">{error}</div>}

        <button className="submit-btn login-submit" onClick={submit} disabled={pending}>
          {pending ? 'Entrando…' : 'Entrar'}
        </button>

        <div className="login-foot">
          Integrações não entram por aqui: usam o header X-API-Key
          (curl, MCP, A2A) — cada credencial na camada dela.
        </div>
      </div>
    </div>
  );
}
