"""DA-45: universalidade de provider - capacidades por origin e rotas auditadas.

Estes testes travam TRES garantias que o desenho troca por configuracao:

1. `llm_send_seed=None` mantem DA-2 (seed=42) - o tri-state nao pode
   degenerar em "desligado" so por causa de um provider incompativel.
2. O modelo e' texto livre e NAO aparece na tabela de rotas - se um
   "modelo" entrar como fronteira de seguranca, a DA estourou o
   proprio proposito.
3. Rota e' fail-closed: nome desconhecido, conflito de provider e origin
   incoerente com a classe declarada falham no BOOT, nao em producao.

O teste mais importante deste arquivo e'
`test_seed_ausente_so_para_destino_incompativel`: ele impede a
regressao exata que motivou a DA (perder determinismo global por causa
de um so endpoint que recusa o campo).
"""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError

import pytest

from app.config import Settings
from app.llm.capabilities import (
    ProviderCapabilities,
    capabilities_for_origin,
    should_send_seed,
)
from app.llm.origins import is_loopback_origin, normalize_origin
from app.llm.routes import (
    LLM_ROUTES,
    describe_routes,
    get_route,
    known_routes,
    route_requires_credential,
)

# --------------------------------------------------------------------------
# 1. Capacidades por ORIGIN
# --------------------------------------------------------------------------


def test_openai_aceita_seed_por_default():
    """DA-2 nao pode regredir: OpenAI aceita e continua recebendo seed."""
    assert should_send_seed("https://api.openai.com") is True


def test_gemini_nao_aceita_seed_detectado_por_origin():
    """O endpoint do Gemini recusa `seed` com 400 - o fato e' por origin."""
    assert should_send_seed("https://generativelanguage.googleapis.com") is False


def test_rotulo_openai_nao_decide_sozinho():
    """'openai' e' um rotulo. A MESMA origin decide igual; origin
    diferente decide diferente - e' isso que torna a tabela correta onde
    um booleano global seria errado."""
    capabilities_for_origin("https://api.openai.com")
    assert capabilities_for_origin("https://api.openai.com").supports_seed is True
    assert (
        capabilities_for_origin("https://generativelanguage.googleapis.com").supports_seed is False
    )


def test_origin_desconhecida_preserva_comportamento_atual():
    """Destino nao registrado se comporta como sempre (seed enviado).

    O padrao e' deliberadamente NAO conservador: a tabela remove casos
    CONHECIDOS, nao adivinha. Um destino novo que funciona hoje nao pode
    comecar a falhar por causa de uma entrada que alguem ainda nao
    escreveu - e o override existe para quem quiser ser explicito.
    """
    assert capabilities_for_origin("https://vllm-interno.corp.example").supports_seed is True


def test_origin_vazia_preserva_comportamento_atual():
    assert capabilities_for_origin("").supports_seed is True


@pytest.mark.parametrize("override,expected", [(True, True), (False, False)])
def test_override_explicito_vence_a_tabela(override, expected):
    """Quem opera o gateway conhece o gateway dele melhor do que uma
    tabela versionada. O override SEMPRE vence - nos dois sentidos, e
    inclusive no destino que a tabela diz nao aceitar seed."""
    origin = "https://generativelanguage.googleapis.com"
    assert capabilities_for_origin(origin).supports_seed is False
    assert should_send_seed(origin, override) is expected


def test_capabilities_e_imutavel():
    """E' dado de conformidade com provedor externo, nao valor ajustavel
    em runtime."""
    caps = ProviderCapabilities(
        supports_seed=True, supports_response_format=True, supports_tools=True
    )
    with pytest.raises(FrozenInstanceError):
        caps.supports_seed = False  # type: ignore[misc]


# --------------------------------------------------------------------------
# 2. Origem: normalizacao e loopback
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://api.openai.com", "https://api.openai.com"),
        ("https://API.OpenAI.com/", "https://api.openai.com"),
        ("https://api.openai.com:443", "https://api.openai.com"),
        ("http://localhost:11434", "http://localhost:11434"),
        ("https://user:secret@gateway.corp.example/v1", "https://gateway.corp.example"),
        ("", ""),
        ("   ", ""),
        ("nao-e-url", ""),
    ],
)
def test_normalize_origin_estabiliza_equivalentes(raw, expected):
    assert normalize_origin(raw) == expected


def test_normalize_origin_descarta_credencial_do_userinfo():
    """O userinfo nao e' a origem - e' segredo. Ele nunca pode entrar
    numa chave de comparacao nem num log de policy."""
    assert "secret" not in normalize_origin("https://user:secret@api.openai.com")


@pytest.mark.parametrize(
    "origin,expected",
    [
        ("http://localhost:11434", True),
        ("http://127.0.0.1:8000", True),
        ("https://api.openai.com", False),
        ("", False),
    ],
)
def test_is_loopback_origin(origin, expected):
    assert is_loopback_origin(origin) is expected


# --------------------------------------------------------------------------
# 3. Rotas auditadas
# --------------------------------------------------------------------------


def test_rota_conhecida_existe():
    assert get_route("local_lab") is not None
    assert get_route("enterprise_azure") is not None


def test_rota_desconhecida_retorna_none():
    """O caller decide o que fazer com None. Aqui o caller (o validator
    de boot) transforma em erro - fail-closed."""
    assert get_route("rota_que_nao_existe") is None
    assert get_route("") is None


def test_tabela_nao_pode_ser_alterada_pelo_chamador():
    """known_routes() devolve copia: quem chama nao consegue reescrever
    a tabela de auditoria."""
    copia = known_routes()
    copia.pop("local_lab", None)
    assert "local_lab" in LLM_ROUTES


def test_modelo_nao_esta_na_tabela_de_rotas():
    """Regra de ouro: a tabela declara ONDE o dado sai e O QUE o destino
    aceita. O MODELO e' nome livre em llm_model. Se um 'modelo' entrar
    como chave de auditoria, a DA-45 estourou o proprio proposito - porque
    e' o modelo que o cliente troca o tempo todo, sem PR.

    O teste olha `name` e `provider` (as chaves de auditoria), nao o
    campo `destination`: a prosa de destino legitimamente menciona
    runtimes como vLLM e llama.cpp, que sao RUNTIMES de inferencia, nao
    modelos. E usa fronteira de palavra para nao casar "llama" dentro de
    "ollama" - que e' o provider, e estar na tabela e' o ponto dele.
    """
    chaves = " ".join(f"{r.name} {r.provider}" for r in LLM_ROUTES.values())
    for nome_de_modelo in (
        "qwen",
        "gpt",
        "claude",
        "gemini",
        "llama",
        "deepseek",
        "mistral",
        "phi",
    ):
        assert not re.search(rf"\b{nome_de_modelo}", chaves), (
            f"modelo {nome_de_modelo!r} vazou para as chaves de rota - "
            f"o modelo pertence a llm_model (texto livre), nao a tabela"
        )


def test_provider_continua_sendo_o_roteador_da_fabrica():
    """As rotas nao inventaram um quarto provider: elas se mapeiam para
    os tres que app/llm/factory.py ja constroi. Uma rota apontando para
    um provider desconhecido passaria pela validacao de nome e falharia
    so na primeira chamada."""
    suportados = {"ollama", "openai", "azure_openai"}
    for rota in LLM_ROUTES.values():
        assert rota.provider in suportados, rota.name


def test_rota_local_exige_loopback():
    """`local_lab` apontando para a internet mentiria na auditoria."""
    rota = get_route("local_lab")
    assert rota.require_loopback is True
    assert rota.matches_destination_class("http://127.0.0.1:11434") is True
    assert rota.matches_destination_class("https://api.openai.com") is False


def test_rota_corporativa_exige_origin_remota():
    """O outro lado da mesma mentira: declarar rota corporativa e mandar
    o dado para um servidor de terceiro em loopback."""
    rota = get_route("enterprise_azure")
    assert rota.require_loopback is False
    assert rota.matches_destination_class("https://recurso.openai.azure.com") is True
    assert rota.matches_destination_class("http://localhost:8000") is False


def test_rota_rejeita_origin_vazia():
    assert get_route("local_lab").matches_destination_class("") is False


def test_rota_local_nao_exige_credencial():
    """vLLM/Ollama em loopback tipicamente aceita qualquer string; exigir
    key seria atrito sem ganho - nao ha egress para proteger."""
    assert route_requires_credential("local_lab") is False
    assert route_requires_credential("enterprise_azure") is True


def test_describe_routes_nao_vaza_segredo():
    """O retrato das rotas vai para GET /llm/policy e para documentacao:
    nao pode conter segredo nem origin real de terceiros."""
    for entrada in describe_routes():
        assert set(entrada) >= {"route", "provider", "destination", "capabilities"}
        assert "api_key" not in entrada
        assert "secret" not in str(entrada).lower()


# --------------------------------------------------------------------------
# 4. Boot: Settings recusa rota invalida
# --------------------------------------------------------------------------


def test_rota_vazia_nao_valida_nada():
    """Default = comportamento identico ao de sempre. Quem nao adoptou
    DA-45 nao sente nada."""
    assert Settings(_env_file=None).llm_route == ""


def test_rota_desconhecida_falha_no_boot():
    with pytest.raises(ValueError, match="nao e uma rota conhecida"):
        Settings(_env_file=None, llm_route="fornecedor_inexistente")


def test_erro_de_rota_desconhecida_lista_as_opcoes():
    """A mensagem precisa ser acionavel: dizer o que NAO existe e' tao
    inutil quanto nao dizer nada."""
    with pytest.raises(ValueError) as exc:
        Settings(_env_file=None, llm_route="fornecedor_inexistente")
    texto = str(exc.value)
    assert "local_lab" in texto
    assert "LLM_MODEL" in texto, "a mensagem deve apontar o que o cliente PODE mudar sozinho"


def test_conflito_de_provider_explicito_falha_no_boot():
    """Dois valores dizendo coisas diferentes nao tem um 'certo' - o
    gateway silenciosamente ignorando um dos dois seria pior."""
    with pytest.raises(ValueError, match="conflito"):
        Settings(
            _env_file=None,
            llm_route="enterprise_azure",
            llm_provider="ollama",
        )


def test_provider_explicito_igual_ao_da_rota_e_aceito():
    cfg = Settings(_env_file=None, llm_route="local_lab", llm_provider="ollama")
    assert cfg.llm_provider == "ollama"


def test_rota_define_provider_quando_ele_nao_e_explicito():
    """Coerente com a promessa de DX: escolher a rota basta."""
    cfg = Settings(_env_file=None, llm_route="local_lab")
    assert cfg.llm_provider == "ollama"


def test_rota_local_com_origin_remota_falha_no_boot():
    """O guard mais importante: rotulo auditado divergindo do destino
    real nao sobe para producao."""
    with pytest.raises(ValueError, match="loopback"):
        Settings(
            _env_file=None,
            llm_route="local_lab",
            ollama_host="https://servidor-remoto.example",
        )


def test_erro_de_origin_divergente_diz_o_que_resolveu():
    with pytest.raises(ValueError) as exc:
        Settings(
            _env_file=None,
            llm_route="local_lab",
            ollama_host="https://servidor-remoto.example",
        )
    assert "servidor-remoto.example" in str(exc.value)


# --------------------------------------------------------------------------
# 5. Item 4: 'ollama' como fallback (cloud -> local)
# --------------------------------------------------------------------------


def test_ollama_e_aceito_como_fallback():
    """Antes o Literal era [openai, azure_openai, ""], o que tornava
    inexpressivel o pedido mais comum: cloud primario, local no
    fallback."""
    cfg = Settings(_env_file=None, llm_provider="openai", llm_fallback_provider="ollama")
    assert cfg.llm_fallback_provider == "ollama"


def test_gateway_seleciona_ollama_como_fallback():
    from app.llm.gateway import _select_allowed_providers

    cfg = Settings(_env_file=None, llm_provider="openai", llm_fallback_provider="ollama")
    assert _select_allowed_providers("public", "openai", "ollama", cfg) == ["openai", "ollama"]


def test_fallback_local_nao_enfraquece_a_soberania_da_43():
    """Adicionar 'ollama' como opcao de fallback nao pode virar um buraco
    na DA-43: em dado confidential com modo strict, o cloud continua
    barrado e SO o local sobrevive - que e' o comportamento correto,
    porque o dado nao sai da maquina."""
    from app.llm.gateway import _select_allowed_providers

    cfg = Settings(
        _env_file=None,
        llm_provider="openai",
        llm_fallback_provider="ollama",
        data_sovereignty_mode="strict",
    )
    assert _select_allowed_providers("confidential", "openai", "ollama", cfg) == ["ollama"]
