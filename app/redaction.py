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
# com pontuacao e o de maior confianca. Verifica ambos, mas exige
# boundary de digito (\b) para nao cortar um numero maior no meio.
_CPF_FORMATTED_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
_CPF_DIGITS_RE = re.compile(r"(?<!\d)\d{11}(?!\d)")

# Numero de IDoc SAP: identificador numerico de 16 digitos (formato
# padrao do campo DOCNUM/IDOCNUMBER, ex: "0000000012345678"). Nao e
# PII no sentido classico, mas identifica um documento de negocio
# especifico - a avaliacao externa pede redaction dele no mesmo item,
# tratado aqui com o mesmo mecanismo.
_IDOC_NUMBER_RE = re.compile(r"(?<!\d)\d{16}(?!\d)")

_REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_EMAIL_RE, "[EMAIL_REDACTED]"),
    (_CPF_FORMATTED_RE, "[CPF_REDACTED]"),
    (_IDOC_NUMBER_RE, "[IDOC_REDACTED]"),
    # CPF sem pontuacao verificado por ULTIMO e so se os 11 digitos
    # ainda nao foram consumidos por outro padrao (ex.: parte de um
    # numero de 16 digitos ja redigido acima) - ordem importa aqui.
    (_CPF_DIGITS_RE, "[CPF_REDACTED]"),
]


def redact_pii_text(text: str | None) -> str:
    """Substitui e-mail/CPF/numero de IDoc por marcadores explicitos.
    None ou string vazia devolve string vazia (mesmo contrato de
    `sanitize_untrusted_input`)."""
    if not text:
        return ""
    redacted = text
    for pattern, marker in _REDACTION_PATTERNS:
        redacted = pattern.sub(marker, redacted)
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
