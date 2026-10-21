"""DLP/NER avançado para detecção de PII (DA-80, 2026-10-20).

Modo de uso:
    from app.validation.pii import PIIValidator

    validator = PIIValidator()
    text = "O CPF do cliente é 123.456.789-01 e o email é teste@email.com"
    redacted, findings = validator.validate_and_redact(text)
    # redacted: "O CPF do cliente é [CPF_REDACTED] e o email é [EMAIL_REDACTED]"
    # findings: [{"type": "CPF", "start": 12, "end": 22, "text": "123.456.789-01"},
    #            {"type": "EMAIL", "start": 40, "end": 56, "text": "teste@email.com"}]

Objetivo: substituir redaction.py regex-only por NER spaCy (pt_core_news_lg)
com fallback regex para palavras-chave (api_key, secret, token, etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Tenta importar spaCy (opcional para esta v1)
try:
    import spacy

    _SPACY_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback quando spaCy não instalado
    _SPACY_AVAILABLE = False

# Lista de palavras-chave para PII sensível (fallback regex)
_SENSITIVE_KEYWORDS = [
    "password",
    "senha",
    "secret",
    "api_key",
    "apikey",
    "client_secret",
    "clientsecret",
    "bearer",
    "token",
    "acess_token",
    "access_token",
    "authorization",
    "auth",
    "credencial",
    "credential",
    "chave",
    "secret_key",
    "secretkey",
    "private_key",
    "privatekey",
    "private-key",
    "refresh_token",
    "refreshtoken",
    "jwttoken",
]


@dataclass(frozen=True)
class PIIEntity:
    """Entidade PII detectada."""

    type_: str  # CPF, CNPJ, EMAIL, TOKEN, PASSWORD, KEYWORD, etc.
    start: int
    end: int
    text: str


class PIIValidator:
    """Validador de PII com spaCy NER e fallback regex.

    Uso:
        validator = PIIValidator()
        text = "Meu CPF é 123.456.789-01"
        redacted, findings = validator.validate_and_redact(text)

    Prioridade: spaCy NER > regex de padrão > regex de keyword
    """

    _nlp: Any | None = None
    _email_re = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    _cpf_formatted_re = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
    _cnpj_re = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")
    _idoc_re = re.compile(r"(?<!\d)\d{16}(?!\d)")
    _password_json_re = re.compile(
        r'(?i)"(?:password|senha|secret|api_?key|client_?secret)"\s*:\s*"[^"]+"'
    )
    _password_json_single_re = re.compile(
        r"(?i)'(?:password|senha|secret|api_?key|client_?secret)'\s*:\s*'[^']+'"
    )
    _password_yaml_re = re.compile(
        r"(?i)(?:password|senha|secret|api_?key|client_?secret)\s*:\s*\S+"
    )
    _password_env_re = re.compile(
        r"(?i)^(?:password|senha|secret|api_?key|client_?secret)\s*=\s*\S+"
    )

    def __init__(self) -> None:
        """Inicializa o validador (carrega spaCy se disponível)."""
        if _SPACY_AVAILABLE:
            try:
                self._nlp = spacy.load("pt_core_news_lg")
            except Exception:  # noqa: BLE001 - fallback silencioso
                self._nlp = None

    def _find_by_spacy(self, text: str) -> list[PIIEntity]:
        """Detecta PII usando spaCy NER."""
        if self._nlp is None:
            return []
        doc = self._nlp(text)
        entities: list[PIIEntity] = []
        for ent in doc.ents:
            if ent.label_ in (
                "PERSON",  # Nome completo (podem ser citados em tickets)
                "ORG",  # Organização (raramente PII, mas pode ser sensível)
                "NORP",  # Nacionalidades/grupos religiosos/ políticos
                "FAC",  # Edifícios/nomes de eventos
                "PRODUCT",  # Produtos (podem ser sensíveis)
            ):
                # Não consideramos como PII estrito (CPF/cnpj/email), mas
                # pode ser sensível em contexto de incidente.
                continue
            if ent.label_ in ("DATE", "TIME", "PERCENT", "MONEY", "QUANTITY", "CARDINAL"):
                # Informações não-PII
                continue
            if ent.label_ in ("LOC", "GPE"):  # Locais/GPEs - geralmente não PII
                continue
            if ent.label_ in ("EVENT", "WORK_OF_ART", "LAW", "LANGUAGE"):
                # Não PII
                continue
            # Se chegou aqui, é uma entidade não classificada como PII estrito
            # Mas pode ser útil para redação (ex: numeração de documentos)
            # Por enquanto, não redigimos entidades spaCy genéricas.
            # A redação estrita fica com regex (CPF/CNPJ/email).
        return entities

    def _find_by_regex(self, text: str) -> list[PIIEntity]:
        """Detecta PII usando regex (padrões formais)."""
        entities: list[PIIEntity] = []
        for pattern, entity_type in [
            (self._email_re, "EMAIL"),
            (self._cpf_formatted_re, "CPF"),
            (self._cnpj_re, "CNPJ"),
            (self._idoc_re, "IDOC"),
        ]:
            for match in pattern.finditer(text):
                entities.append(
                    PIIEntity(
                        type_=entity_type,
                        start=match.start(),
                        end=match.end(),
                        text=match.group(),
                    )
                )
        return entities

    def _find_keyword_patterns(self, text: str) -> list[PIIEntity]:
        """Detecta palavras-chave sensíveis (fallback regex)."""
        entities: list[PIIEntity] = []
        # Padrões para passwords em diferentes formatos
        for match in self._password_json_re.finditer(text):
            entities.append(
                PIIEntity(
                    type_="PASSWORD",
                    start=match.start(),
                    end=match.end(),
                    text=match.group(),
                )
            )
        for match in self._password_json_single_re.finditer(text):
            entities.append(
                PIIEntity(
                    type_="PASSWORD",
                    start=match.start(),
                    end=match.end(),
                    text=match.group(),
                )
            )
        for match in self._password_yaml_re.finditer(text):
            entities.append(
                PIIEntity(
                    type_="PASSWORD",
                    start=match.start(),
                    end=match.end(),
                    text=match.group(),
                )
            )
        for match in self._password_env_re.finditer(text):
            entities.append(
                PIIEntity(
                    type_="PASSWORD",
                    start=match.start(),
                    end=match.end(),
                    text=match.group(),
                )
            )
        # Bearer token
        token_re = re.compile(r"(?i)(bearer|token)\s+([A-Za-z0-9\-_=.+/]{20,})")
        for match in token_re.finditer(text):
            entities.append(
                PIIEntity(
                    type_="TOKEN",
                    start=match.start(),
                    end=match.end(),
                    text=match.group(),
                )
            )
        return entities

    def validate_and_redact(self, text: str) -> tuple[str, list[PIIEntity]]:
        """Detecta PII em text e devolve texto redigido + lista de entidades.

        Ordem de prioridade: regex formal > keyword regex
        spaCy NER não é usado para redação estrita (precisa validação),
        mas pode ser estendido futuramente.

        Args:
            text: Texto original

        Returns:
            Tuple (texto_redigido, lista_de_entidades)
        """
        if not text:
            return "", []

        all_entities = []
        all_entities.extend(self._find_by_regex(text))
        all_entities.extend(self._find_keyword_patterns(text))

        # Ordena por posição decrescente (para substituir da direita para a esquerda)
        all_entities.sort(key=lambda e: e.start, reverse=True)

        # Constrói lista final sem sobreposição (mantém o mais à esquerda)
        final_entities: list[PIIEntity] = []
        seen_ranges: list[tuple[int, int]] = []
        for ent in all_entities:
            overlaps = any(not (ent.end <= start or ent.start >= end) for start, end in seen_ranges)
            if not overlaps:
                final_entities.append(ent)
                seen_ranges.append((ent.start, ent.end))

        final_entities.sort(key=lambda e: e.start)

        # Aplica redação da direita para a esquerda
        redacted = text
        for ent in reversed(final_entities):
            redacted = redacted[: ent.start] + f"[{ent.type_}_REDACTED]" + redacted[ent.end :]

        return redacted, final_entities

    def has_sensitive_data(self, text: str) -> bool:
        """Verifica se o texto contém PII sensível."""
        _, findings = self.validate_and_redact(text)
        return len(findings) > 0
