"""Redaction de PII/dados sensiveis - avaliacao externa (medio prazo,
item 4): "Redaction de PII antes de Langfuse e antes do prompt
(padroes de e-mail, CPF, numeros de IDoc, etc.)".

Antes desta mudanca, so havia truncamento (`_truncate` em
app/agent/nodes.py) - limita TAMANHO, nao CONTEUDO. Um payload/log
colado pelo usuario com um e-mail, CPF ou numero de IDoc real passava
integralmente tanto para o prompt do LLM quanto para o Langfuse (que,
por default, o `@observe` do SDK captura os argumentos E o retorno de
TODA funcao decorada - isto e, o `CopilotState` inteiro, nao so o
prompt final ja sanitizado por `sanitize_untrusted_input`).

Duas funcoes, dois pontos de uso:
  `redact_pii_text(text)`  - usada dentro de
                              `sanitize_untrusted_input` (app/agent/nodes.py),
                              cobre "antes do prompt".
  `redact_pii_deep(data)`  - passada como `mask=` na inicializacao do
                              client Langfuse (app/agent/nodes.py),
                              cobre "antes do Langfuse": aplicada pelo
                              SDK a QUALQUER input/output capturado por
                              `@observe`, nao so ao texto que decidimos
                              sanitizar manualmente.

Defense in depth, nao um DLP completo - cobre os padroes citados
explicitamente na avaliacao (e-mail, CPF, numero de IDoc), regex-based,
sem NER/classificador de PII (fora de escopo desta v1, mesmo
nao-objetivo documentado em app/llm/gateway.py: "PII/DLP de verdade...
permanece pendente" - isto reduz esse gap, nao o fecha por completo).
"""

from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from typing import Any

# E-mail: padrao RFC-simplificado suficiente para o proposito de
# redaction (nao precisa validar e-mail, so reconhecer o formato).
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# CPF: formatado (123.456.789-01) ou so digitos (12345678901) - 11
# digitos e o unico formato de CPF, mas 11 digitos soltos tambem podem
# ser outra coisa (numero de telefone com DDI, por exemplo); o padrao
# com pontuacao e o de maior confianca.
# §3.6: _CPF_DIGITS_RE agora exige contexto — so redige os 11 digitos
# quando precedidos ou seguidos por indicadores de CPF (palavras-chave
# "cpf", "documento", "doc" ou "cadastro" dentro de 30 caracteres).
# Sem esse contexto, 11 digitos anonimos (telefone, serial, OTP, etc.)
# seriam redigidos incorretamente. O padrao FORMATADO (com pontuacao)
# continua sem restricao de contexto, pois e altamente especifico.
_CPF_FORMATTED_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
# Contexto de CPF: "cpf", "documento", "doc:", "cadastro" em ate 30 chars
_CPF_CONTEXT_RE = re.compile(
    r"(?i)(?:cpf|documento|doc\b|cadastro).{0,30}(?<!\d)\d{11}(?!\d)"
    r"|(?<!\d)\d{11}(?!\d).{0,30}(?:cpf|documento|doc\b|cadastro)"
)

# Numero de IDoc SAP: identificador numerico de 16 digitos (formato
# padrao do campo DOCNUM/IDOCNUMBER, ex: "0000000012345678"). Nao e
# PII no sentido classico, mas identifica um documento de negocio
# especifico - a avaliacao externa pede redaction dele no mesmo item,
# tratado aqui com o mesmo mecanismo.
_IDOC_NUMBER_RE = re.compile(r"(?<!\d)\d{16}(?!\d)")

# CNPJ: formatado (12.345.678/0001-99) — 14 digitos, pontuacao obrigatoria
# para diferenciar de outros numeros; sem pontuacao e ambiguo demais.
_CNPJ_RE = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")

# Bearer token / Authorization header — captura o token em si (nao o
# prefixo "Bearer "), para nao redigir o cabecalho inteiro (que pode
# ser util para debug) mas proteger o valor do token.
# Cobre: "Bearer eyJ...", "Authorization: Bearer eyJ..."
_BEARER_TOKEN_RE = re.compile(r"(?i)(?:bearer|token)\s+([A-Za-z0-9\-_=.+/]{20,})")

# Senha em XML/JSON/YAML/env-var — §3.6: cobertura expandida:
#   JSON double-quote: "Password": "valor"       (ja existia)
#   JSON single-quote: 'password': 'valor'       (novo)
#   YAML: password: valor (sem aspas)            (novo)
#   Env-var: API_KEY=valor, CLIENT_SECRET=valor  (novo)
#   XML:  <Password>valor</Password>             (ja existia)
# Usa grupos de captura para preservar a chave/tag e so redigir o valor.
_PASSWORD_JSON_RE = re.compile(
    r'(?i)("(?:password|senha|secret|api_?key|client_?secret)"\s*:\s*)"[^"]+"'
)
_PASSWORD_JSON_SINGLE_RE = re.compile(
    r"(?i)(\'(?:password|senha|secret|api_?key|client_?secret)\'\s*:\s*)\'[^\']+\'"
)
# YAML sem aspas: "password: valor" (valor ate fim de linha ou "#" de comentario)
_PASSWORD_YAML_RE = re.compile(
    r"(?im)^(\s*(?:password|senha|secret|api_?key|client_?secret|apiKey|clientSecret)\s*:\s+)[^\s#\n][^\n]*"
)
# Env-var: API_KEY=valor ou CLIENT_SECRET="valor" (aspas opcionais)
_PASSWORD_ENV_RE = re.compile(
    r'(?i)((?:password|senha|secret|api_?key|client_?secret|apiKey|clientSecret)=)"?[^"\n]+"?'
)
_PASSWORD_XML_RE = re.compile(
    r"(?i)(<(?:password|senha|secret|apikey)>)[^<]*(</)",
    re.IGNORECASE,
)

_REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_EMAIL_RE, "[EMAIL_REDACTED]"),
    (_CNPJ_RE, "[CNPJ_REDACTED]"),
    (_CPF_FORMATTED_RE, "[CPF_REDACTED]"),
    (_IDOC_NUMBER_RE, "[IDOC_REDACTED]"),
    # Bearer token — substitui so o valor, preserva o prefixo para
    # que a linha "Authorization: Bearer [TOKEN_REDACTED]" ainda seja
    # legivel no log.
    (_BEARER_TOKEN_RE, r"[TOKEN_REDACTED]"),
    # Senha em JSON/XML/YAML/env-var — §3.6: preserva a chave/tag.
    (_PASSWORD_JSON_RE, r'\1"[PASSWORD_REDACTED]"'),
    (_PASSWORD_JSON_SINGLE_RE, r"\1'[PASSWORD_REDACTED]'"),
    (_PASSWORD_YAML_RE, r"\1[PASSWORD_REDACTED]"),
    (_PASSWORD_ENV_RE, r"\1[PASSWORD_REDACTED]"),
    (_PASSWORD_XML_RE, r"\1[PASSWORD_REDACTED]\2"),
    # CPF sem pontuacao verificado por ULTIMO - §3.6: a substituicao
    # e feita via funcao auxiliar (_redact_cpf_with_context) que exige
    # contexto de CPF na vizinhanca, para nao redigir numeros de
    # telefone, seriais, OTPs, etc.  Nao vai nesta lista (veja abaixo).
]


def _redact_cpf_digits_with_context(text: str) -> str:
    """§3.6: Redige sequencias de 11 digitos so quando ha contexto de CPF
    na vizinhanca (ate 30 chars antes/depois). Implementacao em dois
    passos para evitar backtracking catastrofico com lookahead variavel:
    1. Encontra todas as posicoes de 11-digitos no texto.
    2. Para cada match, verifica se o contexto de 60 chars ao redor
       contem uma palavra-chave de CPF.
    """
    _CPF_DIGITS_PLAIN = re.compile(r"(?<!\d)\d{11}(?!\d)")
    _CPF_CTX_WORDS = re.compile(r"(?i)\b(?:cpf|documento|cadastro|doc)\b")
    result = list(text)
    offset = 0
    for m in _CPF_DIGITS_PLAIN.finditer(text):
        start, end = m.start(), m.end()
        ctx_start = max(0, start - 30)
        ctx_end = min(len(text), end + 30)
        context = text[ctx_start:ctx_end]
        if _CPF_CTX_WORDS.search(context):
            replacement = "[CPF_REDACTED]"
            result[start + offset : end + offset] = list(replacement)
            offset += len(replacement) - (end - start)
    return "".join(result)


def redact_pii_text(text: str | None) -> str:
    """Substitui e-mail/CPF/numero de IDoc por marcadores explicitos.
    None ou string vazia devolve string vazia (mesmo contrato de
    `sanitize_untrusted_input`)."""
    if not text:
        return ""
    redacted = text
    for pattern, marker in _REDACTION_PATTERNS:
        redacted = pattern.sub(marker, redacted)
    # CPF sem pontuacao: verificado por ultimo, com contexto obrigatorio
    # (§3.6 - evita redigir telefones, seriais e outros numeros de 11 digitos).
    redacted = _redact_cpf_digits_with_context(redacted)
    return redacted


def redact_pii_deep(data: Any) -> Any:
    """Aplica `redact_pii_text` recursivamente a qualquer string dentro
    de `data` (dict, list, tuple, dataclass, pydantic BaseModel ou
    valor escalar), preservando a estrutura. Usada como `mask=` do
    client Langfuse (ver app/agent/nodes.py) - o SDK chama isso com
    `data=<input ou output capturado por @observe>`, que pode ser
    literalmente qualquer coisa (um CopilotState inteiro, uma lista de
    ConnectorResult, etc.), entao esta funcao precisa aceitar
    qualquer tipo sem levantar excecao.
    """
    if isinstance(data, str):
        return redact_pii_text(data)
    if isinstance(data, dict):
        return {key: redact_pii_deep(value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [redact_pii_deep(item) for item in data]
    if is_dataclass(data) and not isinstance(data, type):
        return redact_pii_deep(asdict(data))
    if hasattr(data, "model_dump"):
        # Pydantic BaseModel (ex.: DiagnosisResponse, DiagnosisModel).
        try:
            return redact_pii_deep(data.model_dump())
        except Exception:  # noqa: BLE001 - nunca deixa a mascara quebrar o trace
            return str(data)
    # int/float/bool/None e qualquer outro tipo ja serializavel:
    # devolve como esta.
    return data
