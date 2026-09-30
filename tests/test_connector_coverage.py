"""DA-58: o calculo do mapa de cobertura.

Estes testes nao verificam se a cobertura e' boa — verificam tres coisas
que o mapa depende para nao mentir:

1. `unknown` nunca vira `none` (e nunca vira lacuna), porque "nao sei" e
   "nao tem" sao fatos diferentes e o segundo nao pode ser inventado;
2. conector dedicado e generico nao se confundem, e um conector para varios
   produtos (ariba cobre Ariba E Business Network) nao vira tupla de letras;
3. os dados versionados sao coerentes com o registro real de conectores.

O item 3 e' o unico que roda contra o repo de verdade; os outros dois usam
raiz sintetica para poder introduzir o defeito.
"""

from __future__ import annotations

import pytest
import yaml

from app.evaluation.coverage import (
    AVAILABLE_LEVELS,
    COVERAGE_ABSENT,
    COVERAGE_DEDICATED,
    COVERAGE_GENERIC,
    LEVELS,
    CoverageError,
    build_matrix,
    check_consistency,
    load_all,
    load_coverage,
    load_products,
    render_markdown,
)
from app.evaluation.gates import registered_connectors

MECANISMOS = ("apis", "odata", "rfc")


def _escrever_dados(
    root,
    produtos: list[dict],
    cobertura: list[dict],
    mecanismos: tuple[str, ...] = MECANISMOS,
) -> None:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data/sap_products.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "mechanisms": list(mecanismos), "products": produtos},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data/connector_coverage.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "coverage": cobertura}, sort_keys=False),
        encoding="utf-8",
    )


def _produto(nome: str, **niveis: str) -> dict:
    base = {m: "unknown" for m in MECANISMOS}
    base.update(niveis)
    return {"name": nome, "kind": "sap_product", "mechanisms": base}


def _linha(linhas, nome: str):
    return next(l for l in linhas if l.product.name == nome)


# ---------------------------------------------------------------------------
# unknown != none
# ---------------------------------------------------------------------------


def test_unknown_nao_gera_lacuna(tmp_path) -> None:
    """`unknown` e' ausencia de dado. Contar como lacuna transformaria uma
    verificacao de fonte em trabalho de codigo — e o mapa apontaria o
    dedo para o time errado."""
    _escrever_dados(
        tmp_path,
        [_produto("X", apis="supported")],
        [{"connector": "a", "dedicated_to": "X", "generic_for": [], "mechanisms": ["apis"]}],
    )
    produtos, mecanismos = load_products(tmp_path)
    linhas = build_matrix(produtos, load_coverage(tmp_path), mecanismos)
    # `apis` esta coberto; `odata` e `rfc` sao desconhecidos e NAO viram
    # lacuna. Sem essa separacao o mapa apontaria 2 lacunas que so' se
    # resolvem com uma fonte, nao com codigo.
    assert _linha(linhas, "X").disponiveis == ("apis",)
    assert _linha(linhas, "X").lacunas == ()
    assert _linha(linhas, "X").desconhecidos == ("odata", "rfc")


def test_none_NAO_gera_lacuna_e_none_NAO_e_unknown(tmp_path) -> None:
    """`none` e' negativa da fonte: o produto nao expoe o mecanismo. Nao ha
    o que cobrir, entao nao vira fila de trabalho — mas continua sendo dado,
    nao buraco."""
    _escrever_dados(tmp_path, [_produto("X", apis="none", odata="unknown")], [])
    produtos, mecanismos = load_products(tmp_path)
    linhas = build_matrix(produtos, (), mecanismos)
    linha = _linha(linhas, "X")
    assert linha.lacunas == ()
    assert linha.desconhecidos == ("odata", "rfc")
    assert linha.product.mechanisms["apis"].level != linha.product.mechanisms["odata"].level


@pytest.mark.parametrize("level", sorted(AVAILABLE_LEVELS))
def test_todo_nivel_disponivel_gera_lacuna_sem_cobertura(tmp_path, level: str) -> None:
    _escrever_dados(tmp_path, [_produto("X", apis=level)], [])
    produtos, mecanismos = load_products(tmp_path)
    linhas = build_matrix(produtos, (), mecanismos)
    assert _linha(linhas, "X").lacunas == ("apis",)


def test_level_fora_do_enum_reprovado(tmp_path) -> None:
    _escrever_dados(tmp_path, [_produto("X", apis="maybe")], [])
    with pytest.raises(CoverageError, match="fora do enum"):
        load_products(tmp_path)


def test_enum_tem_unknown_e_none_como_estados_distintos() -> None:
    assert {"unknown", "none"} <= LEVELS
    assert "unknown" not in AVAILABLE_LEVELS
    assert "none" not in AVAILABLE_LEVELS


def test_celula_com_nota_preserva_level_e_nota(tmp_path) -> None:
    """O `MDI` de SuccessFactors e' nome de mecanismo num campo que guarda
    nivel. A nota nao pode contaminar o level, senao `MDI` viraria um
    enesimo 'nivel' e a coluna pararia de responder uma pergunta so."""
    _escrever_dados(
        tmp_path,
        [
            {
                "name": "X",
                "kind": "sap_product",
                "mechanisms": {
                    "apis": "supported",
                    "odata": {"level": "supported", "note": "MDI"},
                    "rfc": "none",
                },
            }
        ],
        [],
    )
    produtos, _ = load_products(tmp_path)
    celula = produtos[0].mechanisms["odata"]
    assert celula.level == "supported"
    assert celula.note == "MDI"
    assert celula.level in LEVELS


# ---------------------------------------------------------------------------
# dedicado vs generico
# ---------------------------------------------------------------------------


def test_dedicado_tem_precedencia_sobre_generico(tmp_path) -> None:
    _escrever_dados(
        tmp_path,
        [_produto("X", odata="supported")],
        [
            {
                "connector": "gen",
                "dedicated_to": None,
                "generic_for": ["X"],
                "mechanisms": ["odata"],
            },
            {"connector": "ded", "dedicated_to": "X", "generic_for": [], "mechanisms": ["odata"]},
        ],
    )
    produtos, mecanismos = load_products(tmp_path)
    linhas = build_matrix(produtos, load_coverage(tmp_path), mecanismos)
    assert _linha(linhas, "X").por_mecanismo["odata"] == COVERAGE_DEDICATED
    assert _linha(linhas, "X").conectores_dedicados == ("ded",)


def test_generico_e_marcado_como_generico_e_nao_dedicado(tmp_path) -> None:
    """O ponto do `generic`: um cliente OData alcançaria o produto se a URL
    apontasse, e isso NAO e' conector feito para ele. Se `generic` virar
    `dedicated`, o mapa passa a afirmar suporte que ninguem testou."""
    _escrever_dados(
        tmp_path,
        [_produto("X", odata="supported")],
        [
            {
                "connector": "odata",
                "dedicated_to": None,
                "generic_for": ["X"],
                "mechanisms": ["odata"],
            }
        ],
    )
    produtos, mecanismos = load_products(tmp_path)
    linhas = build_matrix(produtos, load_coverage(tmp_path), mecanismos)
    assert _linha(linhas, "X").por_mecanismo["odata"] == COVERAGE_GENERIC
    assert _linha(linhas, "X").conectores_dedicados == ()
    assert _linha(linhas, "X").lacunas == ()


def test_dedicado_aceita_lista_de_produtos(tmp_path) -> None:
    """ariba cobre Ariba E Business Network (o docstring diz as duas).
    `tuple("Ariba")` viraria ('A','r','i',...) e casaria com nome de produto
    so por acaso — silenciosamente, num arquivo de dado."""
    _escrever_dados(
        tmp_path,
        [_produto("Ariba", apis="supported"), _produto("Business Network", apis="supported")],
        [
            {
                "connector": "ariba",
                "dedicated_to": ["Ariba", "Business Network"],
                "generic_for": [],
                "mechanisms": ["apis"],
            }
        ],
    )
    produtos, mecanismos = load_products(tmp_path)
    entradas = load_coverage(tmp_path)
    assert entradas[0].dedicated_to == ("Ariba", "Business Network")
    linhas = build_matrix(produtos, entradas, mecanismos)
    assert _linha(linhas, "Ariba").conectores_dedicados == ("ariba",)
    assert _linha(linhas, "Business Network").conectores_dedicados == ("ariba",)


def test_dedicado_aceita_string_simples(tmp_path) -> None:
    _escrever_dados(
        tmp_path,
        [_produto("X", apis="supported")],
        [{"connector": "a", "dedicated_to": "X", "generic_for": [], "mechanisms": ["apis"]}],
    )
    assert load_coverage(tmp_path)[0].dedicated_to == ("X",)


def test_produto_nos_dois_papeis_reprovado(tmp_path) -> None:
    _escrever_dados(
        tmp_path,
        [_produto("X", apis="supported")],
        [
            {
                "connector": "a",
                "dedicated_to": "X",
                "generic_for": ["X"],
                "mechanisms": ["apis"],
            }
        ],
    )
    with pytest.raises(CoverageError, match="dedicated_to"):
        load_coverage(tmp_path)


def test_conector_orfao_reprovado(tmp_path) -> None:
    """Sem destino nenhum, o conector existe no registro e nao aparece no
    mapa: cobertura subestimada sem nenhum sinal visivel."""
    _escrever_dados(
        tmp_path,
        [_produto("X")],
        [{"connector": "a", "dedicated_to": None, "generic_for": [], "mechanisms": ["apis"]}],
    )
    with pytest.raises(CoverageError, match="orfao"):
        load_coverage(tmp_path)


def test_conector_sem_mecanismo_reprovado(tmp_path) -> None:
    _escrever_dados(
        tmp_path,
        [_produto("X")],
        [{"connector": "a", "dedicated_to": "X", "generic_for": [], "mechanisms": []}],
    )
    with pytest.raises(CoverageError, match="mechanisms"):
        load_coverage(tmp_path)


def test_mecanismo_fora_das_dimensoes_reprovado(tmp_path) -> None:
    _escrever_dados(
        tmp_path,
        [
            {
                "name": "X",
                "kind": "sap_product",
                "mechanisms": {
                    "apis": "supported",
                    "odata": "supported",
                    "rfc": "saga",
                    "extra": "x",
                },
            }
        ],
        [],
    )
    with pytest.raises(CoverageError, match="fora das"):
        load_products(tmp_path)


def test_mecanismo_faltante_reprovado(tmp_path) -> None:
    _escrever_dados(
        tmp_path,
        [{"name": "X", "kind": "sap_product", "mechanisms": {"apis": "supported"}}],
        [],
    )
    with pytest.raises(CoverageError, match="sem mecanismo declarado"):
        load_products(tmp_path)


def test_produto_duplicado_reprovado(tmp_path) -> None:
    _escrever_dados(tmp_path, [_produto("X"), _produto("X")], [])
    with pytest.raises(CoverageError, match="duplicado"):
        load_products(tmp_path)


def test_kind_desconhecido_reprovado(tmp_path) -> None:
    _escrever_dados(
        tmp_path,
        [{"name": "X", "kind": "banana", "mechanisms": {m: "none" for m in MECANISMOS}}],
        [],
    )
    with pytest.raises(CoverageError, match="kind"):
        load_products(tmp_path)


# ---------------------------------------------------------------------------
# coerencia com o registro
# ---------------------------------------------------------------------------


def test_check_consistency_acusa_conector_registrado_sem_declaracao(tmp_path) -> None:
    _escrever_dados(
        tmp_path,
        [_produto("X")],
        [{"connector": "a", "dedicated_to": "X", "generic_for": [], "mechanisms": ["apis"]}],
    )
    produtos, _, cobertura, _ = load_all(tmp_path)
    problemas = check_consistency(frozenset({"a", "b"}), produtos, cobertura)
    assert any("b" in p and "sem linha" in p for p in problemas)


def test_check_consistency_acusa_declaracao_fora_do_registro(tmp_path) -> None:
    _escrever_dados(
        tmp_path,
        [_produto("X")],
        [{"connector": "fantasma", "dedicated_to": "X", "generic_for": [], "mechanisms": ["apis"]}],
    )
    produtos, _, cobertura, _ = load_all(tmp_path)
    problemas = check_consistency(frozenset({"a"}), produtos, cobertura)
    assert any("fantasma" in p and "fora do registro" in p for p in problemas)


def test_check_consistency_acusa_produto_fantasma(tmp_path) -> None:
    _escrever_dados(
        tmp_path,
        [_produto("X")],
        [
            {
                "connector": "a",
                "dedicated_to": "ProdutoQueNaoExiste",
                "generic_for": [],
                "mechanisms": ["apis"],
            }
        ],
    )
    produtos, _, cobertura, _ = load_all(tmp_path)
    problemas = check_consistency(frozenset({"a"}), produtos, cobertura)
    assert any("ProdutoQueNaoExiste" in p for p in problemas)


def test_dados_reais_sao_coerentes_com_o_registro_real() -> None:
    """A nona superficie da invariante 23 contra o codigo de verdade, sem
    fixture: e' aqui que um conector novo sem linha de cobertura quebra."""
    produtos, _, cobertura, _ = load_all()
    assert check_consistency(registered_connectors(), produtos, cobertura) == []


# ---------------------------------------------------------------------------
# os dados versionados
# ---------------------------------------------------------------------------


def test_toda_linha_dedicada_tem_conector_registrado() -> None:
    """Nenhuma linha pode se dizer dedicada a um produto sem que exista
    conector correspondente — o mapa nao pode prometer codigo inexistente."""
    _, _, cobertura, _ = load_all()
    registrados = set(registered_connectors())
    for entrada in cobertura:
        assert entrada.connector in registrados, entrada.connector


def test_ariba_cobre_as_duas_linhas_que_o_docstring_declara() -> None:
    produtos, _, cobertura, _ = load_all()
    entrada = next(c for c in cobertura if c.connector == "ariba")
    assert set(entrada.dedicated_to) == {"Ariba", "Business Network"}
    linhas = build_matrix(produtos, cobertura)
    assert _linha(linhas, "Ariba").conectores_dedicados == ("ariba",)
    assert _linha(linhas, "Business Network").conectores_dedicados == ("ariba",)


def test_apim_nao_cobre_integration_suite() -> None:
    """Armadilha registrada: o docstring diz "API Management / Integration
    Suite" e o codigo le eventos de ANALYTICS. Se isto virar cobertura, o
    mapa passa a afirmar que o repo orquestra fluxo por Integration Suite
    para os 22 produtos — e nao orquestra nenhum."""
    produtos, mecanismos, cobertura, _ = load_all()
    entrada = next(c for c in cobertura if c.connector == "apim")
    assert "integration_suite" not in entrada.mechanisms
    linhas = build_matrix(produtos, cobertura, mecanismos)
    for linha in linhas:
        assert linha.por_mecanismo["integration_suite"] == COVERAGE_ABSENT


def test_s4hana_e_generico_e_nao_dedicado() -> None:
    """Nao existe "conector S/4HANA": existem clientes de protocolo. Se o
    mapa disser que temos conector dedicado de S/4HANA, ele inventou um."""
    produtos, mecanismos, cobertura, _ = load_all()
    linhas = build_matrix(produtos, cobertura, mecanismos)
    s4 = _linha(linhas, "S/4HANA")
    assert s4.conectores_dedicados == ()
    assert set(s4.conectores_genericos) == {"odata", "rfc"}
    assert s4.por_mecanismo["odata"] == COVERAGE_GENERIC
    assert s4.por_mecanismo["rfc"] == COVERAGE_GENERIC


def test_linhas_sem_dado_de_capacidade_sao_desconhecidas_e_nao_negativas() -> None:
    """Middleware e nao-SAP existem no mapa porque o repo tem conector. A
    fonte nao os cobre, entao a capacidade e' `unknown` — e nao `none`."""
    produtos, mecanismos, cobertura, _ = load_all()
    linhas = build_matrix(produtos, cobertura, mecanismos)
    for nome in ("ServiceNow", "Salesforce", "Workday", "API Management"):
        linha = _linha(linhas, nome)
        assert len(linha.desconhecidos) == len(mecanismos), nome
        assert not any(linha.product.mechanisms[m].level == "none" for m in mecanismos), nome


def test_render_e_deterministico() -> None:
    """O gate compara TEXTO com o arquivo versionado; duas execucoes
    diferentes produzindo duas strings tornariam o gate instavel."""
    produtos, mecanismos, cobertura, labels = load_all()
    primeiro = render_markdown(build_matrix(produtos, cobertura, mecanismos), mecanismos, labels)
    segundo = render_markdown(build_matrix(produtos, cobertura, mecanismos), mecanismos, labels)
    assert primeiro == segundo


def test_render_nao_inventa_cobertura_para_produto_sem_conector() -> None:
    produtos, mecanismos, cobertura, labels = load_all()
    doc = render_markdown(build_matrix(produtos, cobertura, mecanismos), mecanismos, labels)
    assert "Concur" in doc
    linha = next(l for l in doc.splitlines() if l.startswith("| Concur |"))
    # D = dedicado (0 na linha), G = generico (0 na linha)
    assert linha.count("D") == 0
    assert linha.count("G") == 0
