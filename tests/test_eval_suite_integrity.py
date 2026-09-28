"""Integridade da suite de evaluacao (promptfoo) - o teste que impede a
suite de MEDIR A COISA ERRADA.

O caso que motivou este arquivo: em 2026-09-28 o caso "queue manager"
tinha `assert: contains "broker"` com a palavra "broker" PRESENTE no
texto de entrada. Esse assert nao media raciocinio - mediava se o
provider ecoou o input. Os tres providers "passaram" nesse caso, e parte
desse resultado era artefato do teste, nao do modelo.

Este arquivo nao corrige aquele caso (ja foi corrigido a mao). Ele
trava a INVARIANTE, para que o mesmo tipo de erro nao volte em nenhum
dos tres promptfooconfig*.yaml - inclusive nos que ninguem abre.

Por que isso e uma classe e nao um bug solto: sempre que um assert de
termino compara a saida do modelo contra uma palavra, ha duas formas de
ele ser invalido, e as duas sao silenciosas:

1. TAUTOLOGICO (`contains` com o termo no input): passa por eco.
   Qualquer provider que devolva o input passa - inclusive um ruim.

2. AUTO-CONFIRMANTE (`not-contains` com o termo no input): o termo
   proibido esta no input, entao o provider que o repete no output
   (exatamente o que um modelo fraco faz, copiando o contexto
   recuperado) passa. O assert "prova" o oposto do que quer medir.

O segundo ja aconteceu de verdade: o provider local devolveu
"WebSphere Queue Manager" num caso cujo input citava WebSphere, e o
assert passou. Um teste que reprova Bons modelos e aceita ecos nao tem
poder discriminativo nenhum.
"""

import glob
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGS = sorted(glob.glob(str(REPO_ROOT / "promptfooconfig*.yaml")))


def _casos():
    """Achata todos os configs em (arquivo, indice, caso)."""
    out = []
    for path in CONFIGS:
        data = yaml.safe_load(Path(path).read_text()) or {}
        for i, caso in enumerate(data.get("tests") or []):
            out.append((Path(path).name, i, caso))
    return out


CASOS = _casos()
TERMO_ASSERT = {"contains", "not-contains"}


def test_existe_pelo_menos_um_config_de_evaluacao():
    """Trava de sanidade: se os configs forem renomeados ou apagados, este
    arquivo passa a proteger o nada e da a sensacao de cobertura que nao
    existe. Foi exatamente o que aconteceu com a suite do reraanker."""
    assert CONFIGS, "nenhum promptfooconfig*.yaml encontrado - a invariante parou de valer"
    assert len(CASOS) >= 7, f"esperado >=7 casos medidos, achei {len(CASOS)}"


@pytest.mark.parametrize(
    ("config", "indice", "caso"),
    CASOS,
    ids=[f"{c}-{i}" for c, i, _ in CASOS],
)
def test_valor_de_contains_nao_esta_no_input_do_mesmo_caso(config, indice, caso):
    """`contains` com o termo no input e tautologico: mede eco, nao
    raciocinio. O termo procurado tem de estar AUSENTE do input para que
    qualquer ocorrencia na saida seja invencao do modelo."""
    entrada = " ".join(str(v) for v in (caso.get("vars") or {}).values()).lower()
    for assert_ in caso.get("assert") or []:
        if assert_.get("type") != "contains":
            continue
        valor = str(assert_.get("value", ""))
        assert valor.lower() not in entrada, (
            f"{config} caso[{indice}]: assert `contains {valor!r}` e TAUTOLOGICO - "
            f"o termo ja aparece nas vars de entrada. Qualquer provider que ecoe "
            f"o input passa, entao o caso nao mede raciocinio. Ou remova o assert, "
            f"ou tire o termo da entrada. (Exemplo real: 'broker' no caso de queue "
            f"manager, corrigido em 2026-09-28.)"
        )


@pytest.mark.parametrize(
    ("config", "indice", "caso"),
    CASOS,
    ids=[f"{c}-{i}" for c, i, _ in CASOS],
)
def test_valor_de_not_contains_nao_esta_no_input_do_mesmo_caso(config, indice, caso):
    """`not-contains` com o termo proibido NO INPUT e auto-confirmante: o
    provider que copia o termo do contexto recuperado - que e' justamente o
    comportamento que o assert quer pegar - passa. O termo proibido tem de
    estar ausente do input."""
    entrada = " ".join(str(v) for v in (caso.get("vars") or {}).values()).lower()
    for assert_ in caso.get("assert") or []:
        if assert_.get("type") != "not-contains":
            continue
        valor = str(assert_.get("value", ""))
        assert valor.lower() not in entrada, (
            f"{config} caso[{indice}]: assert `not-contains {valor!r}` e "
            f"AUTO-CONFIRMANTE - o termo proibido esta no input, entao um provider "
            f"que o copia do contexto recuperado passa. O assert mede o oposto do "
            f"que pretende. (Exemplo real: 'WebSphere' no input do caso de queue "
            f"manager na 1a execucao, corrigido em 2026-09-28.)"
        )


@pytest.mark.parametrize(
    ("config", "indice", "caso"),
    CASOS,
    ids=[f"{c}-{i}" for c, i, _ in CASOS],
)
def test_todo_caso_tem_assert_nao_vazio(config, indice, caso):
    """Caso sem assert passa em qualquer provider que devolva JSON
    parseavel. E' o vazamento mais basico de poder discriminativo: um
    teste que nunca falha nao mede nada."""
    asserts = caso.get("assert") or []
    assert asserts, f"{config} caso[{indice}] ({caso.get('description')!r}) nao tem assert"
    for assert_ in asserts:
        assert assert_.get("type") in {
            "contains",
            "not-contains",
            "javascript",
            "is-json",
        }, f"{config} caso[{indice}]: tipo de assert desconhecido {assert_.get('type')!r}"


# Remove um claim de confianca isolado, sobrando o resto do assert. Se
# sobrar nada, o assert NAO tem nenhuma outra afirmacao - e' o defeito.
_CONFIDENCE_CLAIM = re.compile(r"\.confidence\s*(?:<=|>=|<|>)\s*[\d.]+")
_ASSERT_SCAFFOLD = re.compile(r"JSON\.parse\(output\)|const\s+o\s*=|;|\s")


@pytest.mark.parametrize(
    ("config", "indice", "caso"),
    CASOS,
    ids=[f"{c}-{i}" for c, i, _ in CASOS],
)
def test_assert_nao_pode_ter_confianca_como_unica_afirmacao(config, indice, caso):
    """Um assert cuja UNICA afirmacao e sobre `confidence` aceita tanto o
    modelo que se abstem quanto o que acerta o documento errado com numero
    baixo - nao tem poder discriminativo.

    O caso real (identificador desconhecido) citava o documento ERRADO
    (salesforce_case_sap_sync_failure.md) com confidence 0.001 e passava
    em `confidence < 0.6`. O que separa as duas situacoes e'
    matched_source: o guardrail correto devolve null em vez de anexar um
    documento qualquer.

    So e' flagrado quando confidence e' a UNICA coisa checada. Asserts que
    ja tem uma segunda afirmacao sobre conteudo ou modo de falha - como
    `!=/\\b500\\b/.test(...)` ou `/timeout|filtro/i.test(...)` - passam
    intactos: eles medem comportamento, nao so numero."""
    for assert_ in caso.get("assert") or []:
        if assert_.get("type") != "javascript":
            continue
        valor = str(assert_.get("value", ""))
        if not _CONFIDENCE_CLAIM.search(valor):
            continue
        resto = _ASSERT_SCAFFOLD.sub("", _CONFIDENCE_CLAIM.sub("", valor)).strip()
        assert resto, (
            f"{config} caso[{indice}]: `confidence` e' a UNICA afirmacao do "
            f"assert. Passa tanto o modelo que se abstem quanto o que acerta o "
            f"documento errado com numero baixo - nao tem poder discriminativo. "
            f"Para caso de guardrail/seguranca, exija `matched_source === null`; "
            f"para caso de qualidade, adicione uma segunda verificacao sobre "
            f"conteudo ou modo de falha."
        )
