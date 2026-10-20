"""DA-46/48 — modelos ORM do registro de modelos e metering.

Tres tabelas, todas opt-in (so importam sem efeito quando DATABASE_URL
esta configurada — mesmo padrao de app/services/incident_repository.py):

    llm_models        catalogo de modelos por ORIGEM (DA-45: a origem
                      real, e nao o rotulo do provider, e a chave de
                      identidade; o nome do modelo NUNCA entra em tabela
                      de rotas — invariante #8).
    llm_credentials   credencial por origem, CIFRADA com Fernet (DA-47).
    llm_usage         acumulador de tokens reais por (origem, modelo)
                      num periodo aberto (DA-48); percentual consumido
                      e calculado contra llm_models.monthly_limit_tokens.
    integration_systems  catalogo de SISTEMAS integrados (DA-49): o
                      endpoint/vendor/conector real (SAP, Servicenow,
                      Salesforce, Workday, Ariba, CAP, APIM) que aparece
                      como connector_source_system/interface_type nos
                      incidentes. Fase B da superficie admin.
    web_users          USUARIOS DA UI WEB (DA-55): manutencao pelo admin
                      (CRUD em /admin/api/users + tela /admin/users) com
                      ativacao em duas etapas — token por e-mail, depois
                      codigo por telefone — antes do login valer. O hash
                      e' PBKDF2 do mesmo formato da DA-54 (ponto como
                      separador, por causa da interpolacao `$` do compose);
                      login verifica AQUI primeiro e cai no WEB_UI_USERS
                       do .env como bootstrap (mesma chave da DA-54).
    web_search_sources  FONTES DE BUSCA WEB APROVADAS (DA-57): uma linha
                      por `interface_type` (o Literal do pipeline), com o
                      `tech_term` e o `site_filter` que montam a query.
                      Substitui dois mapas literais em `app/agent/nodes.py`.
                      Leitura FAIL-CLOSED: `interface_type` sem linha (ou
                      com linha desabilitada/sem filtro) => sem busca web,
                      e sem fallback em codigo. SEM FK para
                      `integration_systems` pela mesma razao da DA-52: e'
                      configuracao de pesquisa, nao referencia a um sistema.

Colunas foram escolhidas compativeis com PostgreSQL E SQLite de teste
(dialeto async aiosqlite): `Uuid` nativo-driver, sem JSONB, sem tipos
exclusivos de PG — assim os testes unitarios reutilizam os mesmos
modelos de producao em vez de manter um espelho (diferente da abordagem
de test_incident_repository.py, forcada por JSONB/UUID PG do Incident).

Faremos `CREATE TABLE` manualmente por tabela nos testes; o metadata
completo da app e registrado no Alembic (alembic/env.py) para que a
migracao gerada com --autogenerate parta destas definicoes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ---------------------------------------------------------------------------
# llm_models
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


class LlmModel(Base):
    """Modelo de LLM registrado para uso ao runtime (DA-46).

    provider_origin segue o mesmo identificador normalizado da DA-45
    (app/llm/origins.py): "api.openai.com", "api.groq.com", "local_lab",
    etc. — a origem EXISTE (o dado sai para onde a rota declara aceitar),
    independente do rotulo do provider.
    """

    __tablename__ = "llm_models"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_origin: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Precos em USD por 1 milhao de tokens (None = tabela interna do
    # gateway e usada na captura; administrativo nao bloqueia nada).
    price_in_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_out_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Limite mensal de tokens (None = sem limite; percentual fica null).
    monthly_limit_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # DA-72 (migration 020): parametros que o Policy Engine usa para decidir.
    # Nulo = derivado/herdado (comportamento anterior), nunca "permitido por
    # omissao": capacidade nula herda a da ORIGEM (invariante 9 - o modelo so
    # restringe), dominio nulo = todos, transporte/nivel nulos = derivados da
    # origem (app/llm/registry_routing.py).
    transport: Mapped[str | None] = mapped_column(String(32), nullable=True)
    context_window_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supports_tools: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_response_format: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    supports_seed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    domains: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("provider_origin", "model_id", name="uq_llm_models_origin_model"),
    )

    def __repr__(self) -> str:
        return (
            f"<LlmModel origin={self.provider_origin} model={self.model_id} enabled={self.enabled}>"
        )


# ---------------------------------------------------------------------------
# llm_credentials
# ---------------------------------------------------------------------------


class LlmCredential(Base):
    """Credencial de um provider por ORIGEM, cifrada (DA-47, DA-79).

    Uma linha por provider_origin (chave primaria). `encrypted_key` e um
    token Fernet; `masked` guarda a representacao segura para a UI
    (prefixo 4 + '****' + sufixo 4) — a API nunca devolve plaintext.

    DA-79: tenant_id opcional para isolation de credenciais por tenant.
    Quando None, credencial é global. Quando preenchido, (tenant_id,
    provider_origin) torna-se a chave única.
    """

    __tablename__ = "llm_credentials"

    tenant_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="DA-79: tenant identifier for credential isolation. NULL = global credential",
    )
    provider_origin: Mapped[str] = mapped_column(String(128), primary_key=True)
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    masked: Mapped[str] = mapped_column(String(16), nullable=False)
    last_test_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        # DA-79: quando tenant_id não é NULL, (tenant_id, provider_origin) é único
        # when tenant_id is NULL, only provider_origin needs to be unique (global cred)
        # Para PostgreSQL: índice parcial único
        Index(
            "uq_llm_credentials_tenant_origin",
            "tenant_id",
            "provider_origin",
            unique=True,
            postgresql_where=text("tenant_id IS NOT NULL"),
        ),
        # Migration 023 cria este indice parcial para a credencial global.
        # Sem ele no metadata, `alembic check` proporia DROP INDEX.
        Index(
            "uq_llm_credentials_global_origin",
            "provider_origin",
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
        ),
        Index("ix_llm_credentials_tenant", "tenant_id"),
    )

    def __repr__(self) -> str:
        return f"<LlmCredential origin={self.provider_origin} v{self.key_version} masked={self.masked}>"


# ---------------------------------------------------------------------------
# llm_usage
# ---------------------------------------------------------------------------


class LlmUsage(Base):
    """Acumulador de tokens reais por (origem, modelo) num periodo.

    Um periodo aberto por tupla (period_end IS NULL): o metering
    (DA-48) incrementa as colunas; `POST /admin/usage/{origin}/{model}/reset`
    fecha o periodo atual (period_end=agora) e abre outro, permitindo
    percentuais mensais e historico.

    `cost_usd` e a soma do custo calculado na captura (precos do gateway
    ou do registro com prioridade quando disponivel).
    """

    __tablename__ = "llm_usage"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_origin: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        # Unico periodo aberto por (origem, modelo). Indice parcial: em
        # PostgreSQL NULLs sao distintos num UNIQUE simples, entao a
        # unicidade so faz sentido filtrando period_end IS NULL.
        Index(
            "uq_llm_usage_open",
            "provider_origin",
            "model_id",
            unique=True,
            sqlite_where=text("period_end IS NULL"),
            postgresql_where=text("period_end IS NULL"),
        ),
        Index("ix_llm_usage_origin_created", "provider_origin", "period_start"),
    )

    def __repr__(self) -> str:
        return (
            f"<LlmUsage origin={self.provider_origin} model={self.model_id} "
            f"in={self.tokens_in} out={self.tokens_out}>"
        )


# ---------------------------------------------------------------------------
# llm_budget_ledger (DA-67: estimado × real por diagnóstico)
# ---------------------------------------------------------------------------


class LlmBudgetLedger(Base):
    """Ledger de orçamento por diagnóstico (DA-67).

    Registra o orçamento estimado vs real por diagnóstico (run_diagnosis),
    permitindo métricas de desvio e ajuste de heurísticas.

    `estimated_cost_usd`: custo estimado antes da chamada (gateway._estimate_cost_usd)
    `actual_cost_usd`: custo real observado (metering._cost_usd)
    `deviation_ratio`: (actual - estimated) / estimated (se estimated > 0)
    """

    __tablename__ = "llm_budget_ledger"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_origin: Mapped[str] = mapped_column(String(128), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    actual_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    deviation_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens_estimated: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tokens_actual_in: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tokens_actual_out: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        Index("ix_llm_budget_ledger_incident", "incident_id"),
        Index("ix_llm_budget_ledger_origin_created", "provider_origin", "created_at"),
        Index("ix_llm_budget_ledger_provider_origin", "provider_origin"),
    )

    def __repr__(self) -> str:
        return (
            f"<LlmBudgetLedger incident={self.incident_id} origin={self.provider_origin} "
            f"estimated={self.estimated_cost_usd:.4f} actual={self.actual_cost_usd:.4f}>"
        )


# ---------------------------------------------------------------------------
# integration_systems (DA-49, Fase B)
# ---------------------------------------------------------------------------

# Literal fechado dos conectores — o MESMO conjunto de interface_type do
# pipeline (app/models.py::IncidentRequest) para o sistema poder ser
# relacionado ao incidente via connector_type.
# DA-56: `successfactors` faltava aqui. O Literal do pipeline aceitava, o
# supervisor roteava, o CLI e a UI ofereciam — e a correlacao DA-50, que
# resolve incidente->sistema por `connector_type`, nao tinha por onde casar
# um incidente de SuccessFactors com o catalogo. Um conector aceito em todo
# resto do produto e invisivel na unica tela que responde qual sistema e,
# e um conector morto onde a pergunta e feita.
CONNECTOR_TYPES = (
    "odata",
    "rfc",
    "servicenow",
    "salesforce",
    "workday",
    "ariba",
    "successfactors",
    "cap",
    "apim",
    "po",
)

SYSTEM_STATUSES = ("active", "degraded", "offline", "trial")
SYSTEM_ENVIRONMENTS = ("prod", "stage", "dev", "test")


class IntegrationSystem(Base):
    """Sistema integrado gerenciado (DA-49, Fase B da superficie admin).

    E o CATALOGO operacional dos sistemas que aparecem nos incidentes
    (connector_source_system / interface_type): o registro permite ao
    operador manter o mapa SAP/Servicenow/Salesforce/Workday/Ariba/CAP/
    APIM do ambiente, com status e ambiente de deploy — sem editar .env.

    `system_key` e o slug unico (ex: "sap_odata_prod") usado para
    referenciar o sistema de forma estavel. `connector_type` usa o
    Literal fechado do pipeline (odata/rfc/servicenow/salesforce/workday/
    ariba/successfactors/cap/apim) — a ponte natural para correlacionar
    incidentes. O gate `connector_reachable` (DA-51) amarra esta lista ao
    registro real de `app/connectors/__init__.py`, para o docstring nao
    deriva do Literal sem ninguem perceber.
    """

    __tablename__ = "integration_systems"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Unicidade como na migration 004 (UniqueConstraint nomeada, sem indice
    # extra) - divergencia aqui fazia o autogenerate propor DROP/ADD.
    system_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    vendor: Mapped[str] = mapped_column(String(64), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    base_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    environment: Mapped[str] = mapped_column(String(16), default="prod", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("system_key", name="uq_integration_systems_key"),
        Index("ix_integration_systems_vendor_key", "vendor", "system_key"),
    )

    def __repr__(self) -> str:
        return (
            f"<IntegrationSystem key={self.system_key} vendor={self.vendor} "
            f"type={self.connector_type} status={self.status}>"
        )


def percent_consumed(
    *, tokens_in: int, tokens_out: int, monthly_limit_tokens: int | None
) -> int | None:
    """Percentual consumido do limite mensal (None se sem limite)."""
    if not monthly_limit_tokens or monthly_limit_tokens <= 0:
        return None
    total = (tokens_in or 0) + (tokens_out or 0)
    return int(round(total * 100.0 / monthly_limit_tokens, ndigits=0))


class WebUser(Base):
    """Usuario da UI web mantido pelo admin (DA-55).

    Maquina de status (append-descending, sem pulos):

        pending_email -> (token por e-mail) -> pending_phone
        pending_phone -> (codigo por telefone) -> active
        active <-> disabled (pelo admin, reversivel)

    `password_hash` e' o MESMO formato da DA-54 (pbkdf2_sha256.iter.salt
    .hash, ponto como separador). O login (app/auth.py) verifica AQUI
    primeiro e cai no WEB_UI_USERS do .env como bootstrap — o operador
    nunca fica trancado fora por causa do banco.

    `phone_code_hash` guarda so' o HASH do codigo de 6 digitos (nunca o
    codigo), com `phone_code_expires_at`: codigo e' de unica usage e curto
    (10 min). O token de e-mail e' assinado HMAC como o de sessao, com
    namespace proprio ("verify-email:") para nunca colidir com um token
    de sessao.
    """

    __tablename__ = "web_users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    email: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending_email", index=True
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    phone_code_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # SEC-03 (migration 010): erros com o codigo vigente; zera a cada emissao.
    phone_code_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # DA-74 (migration 021): idioma dos e-mails/SMS de ativacao - o da Admin
    # UI de quem cadastrou, ou o `locale` do payload. Nulo (linhas antigas) =
    # pt-BR, o texto de antes da DA-74.
    locale: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="bootstrap")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    def __repr__(self) -> str:
        return f"<WebUser {self.username} status={self.status}>"


# ---------------------------------------------------------------------------
# web_search_sources (DA-57)
# ---------------------------------------------------------------------------


class WebSearchSource(Base):
    """Fonte de busca web APROVADA por `interface_type` (DA-57).

    Antes desta tabela, os dois mapas que montam a query de busca web
    (`_WEB_SEARCH_SITE_MAP` e `tech_term`, ambos em `app/agent/nodes.py`)
    eram literais no codigo, e nenhuma superficie de configuracao os
    tocava. Duas consequencias reais:

    1. `successfactors` (DA-34) e `po` (DA-56) nunca entraram em nenhum dos
       dois mapas. Um incidente desses nao perdia so a lista de sites: perdia
       o `tech_term` tambem, caindo no genérico "SAP integration" — a
       identidade do conector se perdia ANTES do filtro de site, entao
       corrigir so o site map nao bastaria.
    2. `web_search_policy="approved"` nao aprovava nada: o ramo `approved`
       de `_web_search_allowed()` retornava True incondicionalmente. O nome
       prometia uma lista de sites aprovados que nao existia em lugar
       nenhum do codigo.

    Aqui a lista vira dado, com uma linha por `interface_type` (o Literal
    fechado do pipeline). O gate `connector_reachable` (DA-51) foi
    estendido para esta 7a superficie: um conector aceito em todo o resto
    do produto, e sem fonte de pesquisa aprovada, e o mesmo tipo de conector
    morto que a DA-50 e a DA-56 já corrigiram em outras telas.

    FAIL-CLOSED: a ausencia de linha (ou `enabled=False`) significa "sem
    fonte aprovada" e a busca web NAO acontece para aquele interface_type.
    Nao existe fallback hardcoded — e o que mantem o requisito de zero
    hardcoded e evita que a tabela seja contornada por um default em codigo.
    """

    __tablename__ = "web_search_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    interface_type: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    site_filter: Mapped[str] = mapped_column(Text, nullable=False)
    tech_term: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    def __repr__(self) -> str:
        return (
            f"<WebSearchSource {self.interface_type} enabled={self.enabled} "
            f"term={self.tech_term!r}>"
        )


# ---------------------------------------------------------------------------
# llm_runtime_config (DA-XX: overrides de runtime via admin UI)
# ---------------------------------------------------------------------------
# Uma unica linha (pk fixa = 1) que armazena overrides de configuracao de
# infraestrutura que o administrador precisa trocar sem tocar .env nem fazer
# deploy. Quando preenchida, vence o valor do .env.


class LlmRuntimeConfig(Base):
    """Overrides de runtime para o plano LLM (DA-XX).

    Colunas:
        llm_registry_db              — liga o catalogo admin como fonte de
                                       verdade (substitui LLM_REGISTRY_DB)
        llm_provider                 — provider atual (ollama/openai/...)
        llm_route                    — nome da rota auditada (vazio = desligado)
        llm_fallback_provider        — provider de fallback em caso de falha
        llm_credentials_master_key   — chave Fernet para cifrar credenciais
                                       no registro (GAP critico)
        data_sovereignty_mode        — politica de soberania ("strict" ou
                                       "cloud_with_dlp")
        confidential_allowed_origins — origens permitidas para dados confidenciais
        require_auth                 — exige chave API para subir o processo
        rule_engine_enabled          — liga/desliga o motor de regras (DA-33)
        diagnosis_timeout_seconds    — timeout do grafo de diagnostico (seg.)
        llm_request_timeout_seconds  — timeout de chamada LLM (seg.)
    """

    __tablename__ = "llm_runtime_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # sempre 1
    llm_registry_db: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    llm_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_route: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_fallback_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_credentials_master_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    data_sovereignty_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidential_allowed_origins: Mapped[str | None] = mapped_column(String(512), nullable=True)
    require_auth: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rule_engine_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    diagnosis_timeout_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_request_timeout_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # DA-72 (migration 020): orcamento (DA-67) e pesos do score de roteamento
    diagnosis_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_budget_reserve_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_gateway_max_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_routing_cost_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_routing_latency_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Colunas que existem no schema desde deploys anteriores, mas nao estao no
    # conjunto editavel da Admin UI. O gateway usa Settings; manter no ORM
    # alinha metadata e banco sem descartar valores ja persistidos.
    llm_gateway_circuit_failure_threshold: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    llm_gateway_circuit_cooldown_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_gateway_backoff_base_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_gateway_backoff_max_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)


# ---------------------------------------------------------------------------
# llm_call_latency (DA-72: latencia por chamada para o score de roteamento)
# ---------------------------------------------------------------------------


class LlmCallLatency(Base):
    """Uma linha por chamada LLM bem-sucedida. O score usa a mediana recente
    por (origem, modelo); falhas ficam fora para nao premiar um destino que
    responde rapido com erro."""

    __tablename__ = "llm_call_latency"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    provider_origin: Mapped[str] = mapped_column(String(128), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_llm_call_latency_model_created", "provider_origin", "model_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# llm_metrics (DA-70: FinOps metrics)
# ---------------------------------------------------------------------------


class LlmRequests(Base):
    """Contagem de chamadas LLM por provider/model (DA-70).

    Tabela usada para dashboards Grafana. Usa upsert para evitar
    duplicatas em caso de concorrência.
    """

    __tablename__ = "llm_requests"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, primary_key=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False, primary_key=True)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)

    __table_args__ = (
        Index("ix_llm_requests_created_at", "created_at"),
        Index("ix_llm_requests_provider", "provider"),
    )


class LlmTokens(Base):
    """Tokens LLM (input/output) por provider/model (DA-70).

    Tabela usada para dashboards Grafana. Usa upsert para evitar
    duplicatas em caso de concorrência.
    """

    __tablename__ = "llm_tokens"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, primary_key=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False, primary_key=True)
    token_type: Mapped[str] = mapped_column(
        String(10), nullable=False, primary_key=True
    )  # 'input' | 'output'
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)

    __table_args__ = (
        Index("ix_llm_tokens_created_at", "created_at"),
        Index("ix_llm_tokens_provider", "provider"),
    )


class LlmCost(Base):
    """Custo LLM em USD por provider/model (DA-70).

    Tabela usada para dashboards Grafana. Usa upsert para evitar
    duplicatas em caso de concorrência.
    """

    __tablename__ = "llm_cost"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, primary_key=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False, primary_key=True)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (
        Index("ix_llm_cost_created_at", "created_at"),
        Index("ix_llm_cost_provider", "provider"),
    )


# ---------------------------------------------------------------------------
# knowledge_document_versions (DA-86: UI versionamento RAG)
# ---------------------------------------------------------------------------
class KnowledgeDocumentVersion(Base):
    """Versão de documento de conhecimento para rollback (DA-86).

    Permite manter histórico de versões de documentos RAG e fazer rollback
    para versões anteriores. Um documento pode ter várias versões, sendo
    exatamente uma marcada como `is_current=True`.

    Campos:
        document_id: UUID do documento (externo ao Qdrant, identifica o arquivo)
        version: Número sequencial da versão (incremental por documento)
        content: Conteúdo completo do documento naquela versão
        created_at: Timestamp da criação da versão
        is_current: Flag de qual versão está ativa (apenas uma por documento)
    """

    __tablename__ = "knowledge_document_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_knowledge_document_version"),
        # Migration 026 usa um indice unico parcial (nao UniqueConstraint:
        # SQLAlchemy nao aceita postgresql_where em UniqueConstraint).
        Index(
            "uq_knowledge_document_current",
            "document_id",
            "is_current",
            unique=True,
            sqlite_where=text("is_current = 1"),
            postgresql_where=text("is_current = true"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeDocumentVersion doc={self.document_id} v{self.version} "
            f"current={self.is_current}>"
        )


# ---------------------------------------------------------------------------
# snapshot_table / rollback_log (DA-82: rollback de deployment)
# ---------------------------------------------------------------------------
class Snapshot(Base):
    """Snapshot de schema criado antes de um deploy (DA-82).

    A tabela nasceu na migration aa5bc901f443 sem ORM. Sem o modelo aqui,
    `Base.metadata` nao a conhecia e `alembic revision --autogenerate`
    proporia DROP TABLE. O endpoint de rollback foi removido por nao ter
    repositorio real; o schema permanece para a DA-82 (schema-only).
    """

    __tablename__ = "snapshot_table"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (Index("ix_snapshot_table_created", "created_at"),)


class RollbackLog(Base):
    """Registro de rollbacks executados (DA-82).

    Mesmo caso do Snapshot: tabela criada por migration direta, agora
    representada no metadata ORM para o `alembic check` nao enxergar drift.
    """

    __tablename__ = "rollback_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    target_schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (Index("ix_rollback_log_created", "created_at"),)
