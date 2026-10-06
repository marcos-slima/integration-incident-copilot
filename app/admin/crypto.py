"""DA-47 — cifra Fernet para credenciais de provedores em repouso.

A master key ficaria no .env (LLM_CREDENTIALS_MASTER_KEY): o UNICO
segredo que permanece fora do banco depois que as chaves dos provedores
migram para o registro (app/admin/). Toda escrita usa `encrypt_secret`;
a unica leitura de plaintext e `decrypt_secret`, usada pelo runtime
(app/admin/runtime.py, quando LLM_REGISTRY_DB=true) — nenhuma rota
/admin/devolve plaintext (mask_secret para exibicao).

Decisoes:
  - Fernet (AES-128-CBC + HMAC-SHA256), do pacote `cryptography`:
    cifra autenticada, format-padrao da lib, sem gerenciar
    IV/versionamento de token manualmente por aqui.
  - Master key ausente/invalida = ConfigurationError explicito. NUNCA
    gerar key nova em runtime: uma key nova tornaria indecifravel tudo
    o que ja esta no banco (e o operador nao saberia qual key usar).
  - `mask_secret` e deterministica e nunca expoe o texto claro (prefixo
    4 + '****' + sufixo 4).
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

_MASTER_KEY_ENV = "LLM_CREDENTIALS_MASTER_KEY"


def _get_fernet():
    """Fernet instanciado com a master key do settings.

    Levanta ConfigurationError se a master key estiver ausente ou for
    invalida — o operador precisa fixa-la/gira-la via .env, nunca a
    gerencia se resolve sozinha em runtime."""
    from cryptography.fernet import Fernet

    raw = (settings.llm_credentials_master_key or "").strip()
    if not raw:
        raise ConfigurationError(
            f"{_MASTER_KEY_ENV} nao configurada para cifrar/decifrar credenciais "
            "do registro de modelos (DA-47). Gere uma chave com: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())" e fixe no .env.'
        )
    try:
        return Fernet(raw.encode())
    except Exception as exc:  # InvalidFernetToken / binario invalido
        raise ConfigurationError(
            f"{_MASTER_KEY_ENV} invalida — esperada uma Fernet key de "
            "44 chars em URL-safe base64 (veja o comando acima)."
        ) from exc


def encrypt_secret(plaintext: str) -> str:
    """Cifra um secret e devolve o token Fernet (string)."""
    if not plaintext:
        raise ConfigurationError("encrypt_secret exige texto nao-vazio")
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    """Decifra um token Fernet gravado em llm_credentials.

    Erro e ConfigurationError com mensagem orientada a operacao
    (master key trocou?) — nunca inclui o token no log."""
    if not token:
        raise ConfigurationError("decrypt_secret exige token nao-vazio")
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except Exception as exc:
        raise ConfigurationError(
            "Falha ao decifrar credencial do registro (DA-47). Confirme que "
            "LLM_CREDENTIALS_MASTER_KEY e a mesma usada na gravacao da chave."
        ) from exc


def encrypt_evidence(evidence: Any) -> str | None:
    """Cifra evidence_json (list[dict]) usando Fernet (DA-60)."""
    if evidence is None:
        return None
    try:
        from json import dumps

        payload = dumps(evidence, ensure_ascii=False)
        return _get_fernet().encrypt(payload.encode()).decode()
    except Exception:  # noqa: BLE001
        logger.warning("[crypto] Falha ao cifrar evidence_json, deixando em claro")
        return None


def decrypt_evidence(token: str | dict | list | None) -> list[dict] | None:
    """Decifra um token Fernet de evidence_json (DA-60)."""
    # Se já é uma estrutura Python (dict ou list), devolve direto
    # (suporte a dados antigos, sqlite ou SQLite sem cifra)
    if isinstance(token, (dict, list)):
        return token
    if not token:
        return None
    # Se não é string cifrada (não começa com "gAAA"), tenta JSON direto
    # (suporte a dados antigos ou ambientes sem criptografia)
    if isinstance(token, str) and not token.startswith("gAAA"):
        try:
            from json import loads

            return loads(token)
        except Exception:  # noqa: BLE001
            return None
    try:
        from json import loads

        payload = _get_fernet().decrypt(token.encode()).decode()
        return loads(payload)
    except Exception as exc:
        raise ConfigurationError(
            "Falha ao decifrar evidence_json (DA-60). Confirme que "
            "LLM_CREDENTIALS_MASTER_KEY e a mesma usada na gravacao."
        ) from exc


def mask_secret(plaintext: str) -> str:
    """Exibicao segura: prefixo 4 + '****' + sufixo 4 (funciona p/
    chaves curtas e longas). Nunca devolve o texto em claro."""
    if not plaintext:
        return ""
    if len(plaintext) <= 8:
        return "****"
    return f"{plaintext[:4]}****{plaintext[-4:]}"


def master_key_configured() -> bool:
    return bool((settings.llm_credentials_master_key or "").strip())
