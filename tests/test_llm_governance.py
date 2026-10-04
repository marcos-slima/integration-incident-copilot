"""DA-43 (governanca de saida de dado) - prova de que dado CONFIDENCIAL
nao sai do perímetro, e de que a policy é auditável por um terceiro.

Diferença em relação a tests/test_llm_gateway.py (DA-26), que testa a
seleção de provider: aqui o foco não é QUEM é chamado, mas QUE o LLM
chamado não existe quando a policy nega. Por isso o teste central
verifica que `get_chat_model` não foi sequer INSTANCIADO - uma exceção
subida sem construir o cliente prova muito menos do que a ausência da
construção, porque um cliente construído já carrega endpoint e chave.
"""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.connectors.base import ConnectorResult
from app.llm import gateway
from app.llm.gateway import (
    PolicyViolationError,
    _provider_allows_sensitivity,
    _select_allowed_providers,
    circuit_breaker,
    describe_effective_policy,
    invoke_via_gateway,
    normalize_origin,
    resolve_provider_origin,
)
from app.main import app


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


def _confidential_state():
    """Estado com dado REAL de conector - classifica como 'confidential'
    (mesmo sinal de DA-15/DA-25)."""
    return {
        "connector_data": ConnectorResult(
            source_system="OData",
            status="error",
            error_code="401",
            message="Unauthorized",
            raw="{}",
            is_mock=False,
            is_fallback=False,
        )
    }


# Origens cloud usadas nos testes. Todas sao providers REAIS usados em
# integracao; nenhuma delas e a OpenAI oficial.
CLOUD_ORIGINS = [
    "https://api.groq.com/openai/v1",
    "https://generativelanguage.googleapis.com/v1beta/openai/",
    "https://api.openai.com/v1",
    "https://meu-tenant.openai.azure.com/openai/",
    "http://vllm.dmz.local:8000/v1",
    "https://proxy.interno.corp/v1",
]


# --------------------------------------------------------------------------
# normalize_origin
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "esperado"),
    [
        # o que o cliente digita vs. o que a policy compara
        ("https://API.OpenAI.com/v1", "https://api.openai.com"),
        ("https://api.openai.com:443", "https://api.openai.com"),
        ("https://api.openai.com/", "https://api.openai.com"),
        ("  https://api.openai.com  ", "https://api.openai.com"),
        # porta NAO-default e significativa (self-hosted em :8000)
        ("http://vllm.local:8000/v1", "http://vllm.local:8000"),
        # credencial embutida e DESCARTADA: a policy compara destino,
        # nao segredo. Tambem garante que a origem nunca vaza userinfo
        # para o endpoint de auditoria.
        ("https://user:super-secreto@api.openai.com/v1", "https://api.openai.com"),
        # entradas invalidas -> string vazia, nunca permissiva
        ("", ""),
        ("   ", ""),
        ("nao-e-url", ""),
        ("/caminho/sem/host", ""),
    ],
)
def test_normalize_origin_compara_destino_nao_texto(raw, esperado):
    assert normalize_origin(raw) == esperado


# --------------------------------------------------------------------------
# resolve_provider_origin: a policy e por ORIGEM, nao por rotulo
# --------------------------------------------------------------------------


def test_mesmo_rotulo_openai_resolve_destinos_diferentes():
    """O heart da DA-43. 'openai' e um rotulo de cliente, nao um lugar:
    sem OPENAI_BASE_URL vai para api.openai.com; com ela, para qualquer
    endpoint compativel. Medido no comparativo promptfoo: Gemini e Groq
    sao AMBOS llm_provider='openai' e so mudam OPENAI_BASE_URL."""
    padrao = Settings()
    assert resolve_provider_origin("openai", padrao) == "https://api.openai.com"

    cfg = padrao.model_copy(update={"openai_base_url": "https://api.groq.com/openai/v1"})
    assert resolve_provider_origin("openai", cfg) == "https://api.groq.com"


def test_origem_de_provider_desconhecido_vazia_e_como_provider_cloud():
    """Provider fora do Literal nao resolve origem -> niega. Sem isso um
     typo em LLM_PROVIDER seria classificado como cloud e poderia
    escapar do filtro de soberania."""
    cfg = Settings(llm_provider="ollama")
    ok, motivo = _provider_allows_sensitivity("llm_desconhecido", "confidential", cfg)
    assert ok is False
    assert "nao resolvida" in motivo


# --------------------------------------------------------------------------
# A garantia central: confidential NUNCA sai para cloud nao-allowlisted
# --------------------------------------------------------------------------


@pytest.mark.parametrize("origin", CLOUD_ORIGINS)
def test_confidential_nao_sai_para_cloud_sem_allowlist(origin):
    """Modo cloud_with_dlp SEM allowlist nega TODO cloud. Este e o
    comportamento FAIL-CLOSED: o modo sozinho nao libera nada, e' preciso
    nomear o destino."""
    cfg = Settings(
        llm_provider="openai",
        data_sovereignty_mode="cloud_with_dlp",
        openai_base_url=origin,
        confidential_allowed_origins="",
    )
    assert _select_allowed_providers("confidential", "openai", "", cfg) == []


@pytest.mark.parametrize("origin", CLOUD_ORIGINS)
def test_confidential_so_sai_para_origem_allowlisted(origin):
    """Alem do modo, a ORIGIN precisa estar nomeada. allowlisting da
     OpenAI oficial nao autoriza Groq/Gemini/Azure/vLLM/proxy - todos
    mesmo rotulo 'openai'."""
    cfg = Settings(
        llm_provider="openai",
        data_sovereignty_mode="cloud_with_dlp",
        openai_base_url=origin,
        confidential_allowed_origins="https://api.openai.com",
    )
    permitido = _select_allowed_providers("confidential", "openai", "", cfg)
    # so a origem explicitamente nomeada passa
    esperado = (
        ["openai"] if resolve_provider_origin("openai", cfg) == "https://api.openai.com" else []
    )
    assert permitido == esperado


def test_allowlist_casa_por_forma_normalizada():
    """Uma entrada escrito a mao com path/porta/case diferente ainda
    casa com o destino real - senao o operador acha que liberou e a
    policy nega em silencio (ou o contrario, que e pior)."""
    cfg = Settings(
        llm_provider="openai",
        data_sovereignty_mode="cloud_with_dlp",
        openai_base_url="https://api.openai.com/v1",
        confidential_allowed_origins="https://API.OpenAI.com:443/",
    )
    assert _select_allowed_providers("confidential", "openai", "", cfg) == ["openai"]


def test_entrada_invalida_na_allowlist_nao_permite_nada():
    """Allowlist com lixo nao pode virar 'permitir tudo'."""
    cfg = Settings(
        llm_provider="openai",
        data_sovereignty_mode="cloud_with_dlp",
        openai_base_url="https://api.groq.com/openai/v1",
        confidential_allowed_origins="*,*,lixo",
    )
    assert _select_allowed_providers("confidential", "openai", "", cfg) == []


def test_strict_nunca_liberado_pela_allowlist():
    """'strict' e um hard override: mesmo com a origem na allowlist, modo
    strict nega cloud. Belt-and-suspenders para o deploy on-prem que
    trocou o nome do provider mas esqueceu a allowlist."""
    cfg = Settings(
        llm_provider="openai",
        data_sovereignty_mode="strict",
        openai_base_url="https://api.openai.com/v1",
        confidential_allowed_origins="https://api.openai.com",
    )
    assert _select_allowed_providers("confidential", "openai", "", cfg) == []


def test_dado_public_nao_e_restrito():
    """'public' nao e restringido por soberania - o teto de custo do
    gateway e que limita esse caminho."""
    cfg = Settings(llm_provider="openai", data_sovereignty_mode="strict")
    assert _select_allowed_providers("public", "openai", "", cfg) == ["openai"]


# --------------------------------------------------------------------------
# Fail-closed de configuracao
# --------------------------------------------------------------------------


def test_modo_sovereignty_invalido_falha_no_boot():
    """'local_only' (valor invalido que estava no TROUBLESHOOTING.md)
    precisa falhar no pydantic, nunca ser interpretado como
    permissivo."""
    with pytest.raises(ValueError):
        Settings(data_sovereignty_mode="local_only")


def test_modo_sovereignty_invalido_em_settings_sem_validacao_nega():
    """Settings montado sem passar pelo pydantic (model_copy, dict cru)
    ainda e fail-closed. Antes da DA-43 o `else` do gateway tratava
    qualquer valor desconhecido como cloud_with_dlp - ou seja, um typo
    DESLIGAVA a protecao. Este teste trava isso."""
    cfg = Settings().model_copy(update={"data_sovereignty_mode": "local_only"})
    assert _select_allowed_providers("confidential", "openai", "", cfg) == []


def test_origem_ausente_no_azure_nega_confidential():
    """azure_openai sem endpoint configurado nao pode ser considerado
    'aprovado por padrao'."""
    cfg = Settings(
        llm_provider="azure_openai",
        data_sovereignty_mode="cloud_with_dlp",
        azure_openai_endpoint="",
        confidential_allowed_origins="https://api.openai.com",
    )
    assert _select_allowed_providers("confidential", "azure_openai", "", cfg) == []


# --------------------------------------------------------------------------
# Prova executavel: o LLM nao e CONSTRUIDO quando a policy nega
# --------------------------------------------------------------------------


@pytest.mark.parametrize("origin", CLOUD_ORIGINS)
def test_policy_negada_nao_constroi_nenhum_llm(monkeypatch, origin):
    """A prova mais forte disponivel: quando a policy nega, nenhum
    cliente de LLM e criado. Verificar so a excecao seria fraco - um
    cliente ja instanciado carrega endpoint e credencial do cloud.

    Este e o teste que responde 'e se um cliente mandar log de cliente
    para a nuvem?' com evidencia executavel, nao com promessa.
    """
    construidos: list[str] = []

    def _spy(*args, **kwargs):
        construidos.append(str(kwargs.get("model_name")))
        raise AssertionError("get_chat_model NAO deveria ser chamado em policy negada")

    monkeypatch.setattr(gateway, "get_chat_model", _spy)

    cfg = Settings(
        llm_provider="openai",
        llm_fallback_provider="",
        data_sovereignty_mode="strict",
        openai_base_url=origin,
    )
    with pytest.raises(PolicyViolationError) as exc:
        invoke_via_gateway(
            build_and_invoke=lambda llm: pytest.fail("LLM nunca deveria ser invocado"),
            state=_confidential_state(),
            prompt_text="log SAP com dado de cliente",
            config=cfg,
        )

    assert construidos == []
    # a mensagem precisa dizer ONDE o dado teria ido e POR QUE foi negado
    assert normalize_origin(origin) in str(exc.value)
    assert "cloud_with_dlp" in str(exc.value)


def test_policy_permitida_constroi_o_llm(monkeypatch):
    """Contrapositivo: quando a policy permite, o cliente E construido e
    chamado. Garante que o teste anterior nao passa so porque o caminho
    esta quebrado."""
    construidos: list[str] = []
    invocados: list[str] = []

    def _spy(*args, **kwargs):
        construidos.append("get_chat_model")
        return object()

    monkeypatch.setattr(gateway, "get_chat_model", _spy)
    cfg = Settings(
        llm_provider="openai",
        data_sovereignty_mode="cloud_with_dlp",
        openai_base_url="https://api.openai.com/v1",
        confidential_allowed_origins="https://api.openai.com",
    )

    def _invoke(llm):
        invocados.append("build_and_invoke")
        return "resposta"

    resultado, provider = invoke_via_gateway(
        build_and_invoke=_invoke,
        state=_confidential_state(),
        prompt_text="log SAP",
        config=cfg,
    )
    assert resultado == "resposta"
    assert provider == "openai"
    assert construidos == ["get_chat_model"]
    assert invocados == ["build_and_invoke"]


# --------------------------------------------------------------------------
# Superficie de auditoria (o questionario de seguranca do cliente)
# --------------------------------------------------------------------------


def test_policy_descreve_todo_provider_com_origem_e_motivo():
    d = describe_effective_policy(
        Settings(
            llm_provider="openai",
            llm_fallback_provider="azure_openai",
            data_sovereignty_mode="strict",
            openai_base_url="https://api.groq.com/openai/v1",
        )
    )
    assert d["data_sovereignty_mode"] == "strict"
    assert d["providers"]["openai"]["origin"] == "https://api.groq.com"
    assert d["providers"]["openai"]["may_receive_confidential"] is False
    assert d["providers"]["ollama"]["may_receive_confidential"] is True
    # no modo strict, nem o provider local e roteado se ele nao for o
    # primario configurado: a matriz descreve o provider, o roteamento
    # descreve o que sera realmente chamado.
    assert d["routing"]["allowed_for_confidential"] == []
    # azure_openai esta no fallback mas nao tem endpoint configurado, logo
    # nao resolve origem e nem entra no roteamento de 'public' - provider
    # nao configurado nao pode ser chamado (fail-closed), mesmo para
    # dado public.
    assert d["routing"]["allowed_for_public"] == ["openai"]
    assert d["providers"]["azure_openai"]["may_receive_public"] is False
    assert "nao resolvida" in d["providers"]["azure_openai"]["public_reason"]
    # motivo legivel em toda decisao negativa
    assert "cloud_with_dlp" in d["providers"]["openai"]["confidential_reason"]


def test_policy_efetiva_nao_vaza_nenhuma_credencial():
    """O endpoint de auditoria responde a um cliente com X-API-Key; ele
    nao pode devolver a chave que autentica esse mesmo cliente."""
    cfg = Settings(
        llm_provider="openai",
        openai_base_url="https://user:s3cr3t-in-url@api.groq.com/openai/v1",
        openai_api_key="chave-ficticia-nao-e-real",
        azure_openai_api_key="outra-chave-ficticia",
    )
    d = describe_effective_policy(cfg)
    serializado = repr(d)
    for segredo in ("s3cr3t-in-url", "chave-ficticia-nao-e-real", "outra-chave-ficticia", "user:"):
        assert segredo not in serializado, f"credencial vazou no audit: {segredo}"


def test_audit_snapshot_nao_tem_efeito_colateral():
    """describe_effective_policy e leitura pura: nao constroi LLM, nao
    registra falha no circuit breaker, nao cobra."""
    circuit_breaker.reset()
    cfg = Settings(llm_provider="openai", data_sovereignty_mode="strict")
    describe_effective_policy(cfg)
    assert circuit_breaker.consecutive_failures("openai") == 0


# --------------------------------------------------------------------------
# Superficie HTTP: a mesma protecao que o /diagnose, no audit trail
# --------------------------------------------------------------------------


def test_llm_policy_exige_api_key(monkeypatch):
    """O audit trail nao pode ser leitura aberta: expor 'para onde vai
    meu dado' sem autenticacao seria um mapa do atacante."""
    monkeypatch.setattr(main_module, "settings", Settings(api_key="segredo-da-policy"))
    c = TestClient(app)
    assert c.get("/llm/policy").status_code == 401
    assert c.get("/llm/policy", headers={"X-API-Key": "chave-errada"}).status_code == 401
    ok = c.get("/llm/policy", headers={"X-API-Key": "segredo-da-policy"})
    assert ok.status_code == 200


def test_llm_policy_responde_com_origem_e_motivo(monkeypatch):
    """Prova na fronteira HTTP o que o cliente de seguranca le: a origem
    real, a decisao e o motivo."""
    monkeypatch.setattr(main_module, "settings", Settings(api_key="segredo-da-policy"))
    monkeypatch.setattr(
        main_module,
        "describe_effective_policy",
        lambda: describe_effective_policy(
            Settings(
                llm_provider="openai",
                data_sovereignty_mode="cloud_with_dlp",
                openai_base_url="https://api.groq.com/openai/v1",
                confidential_allowed_origins="https://api.openai.com",
                openai_api_key="chave-que-nao-deve-vazar",
            )
        ),
    )
    c = TestClient(app)
    r = c.get("/llm/policy", headers={"X-API-Key": "segredo-da-policy"})
    assert r.status_code == 200
    body = r.json()
    # o rotulo e 'openai', mas a origem revela que e Groq
    assert body["providers"]["openai"]["origin"] == "https://api.groq.com"
    assert body["providers"]["openai"]["may_receive_confidential"] is False
    assert body["confidential_allowed_origins"] == ["https://api.openai.com"]
    assert "chave-que-nao-deve-vazar" not in r.text
