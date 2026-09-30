"""DA-58: mapa de cobertura produto SAP x mecanismo de integracao.

Este modulo CALCULA o mapa. Nao mantem o mapa. O dado vive em
`data/sap_products.yaml` (produto x mecanismo) e
`data/connector_coverage.yaml` (conector -> produto e mecanismo); a saida
Markdown e' gerada por `scripts/coverage_map.py` e versionada em
`docs/COVERAGE_MAP.md`, onde um gate confere que continua em dia.

O mapa responde a uma pergunta que as outras superficies do repo nao
respondem: dado um produto SAP e um mecanismo, TEMOS ALGUM CODIGO QUE FALA
COM ELE, e o que sabemos desse codigo alem de existir?

TRES NIVEIS, e a distincao entre eles e' o trabalho inteiro:

- `dedicated`: existe um conector FEITO para o produto. Unico nivel
  afirmavel sem ressalva.
- `generic`: existe um cliente de MECANISMO (OData, RFC) que alcançaria o
  produto se a URL apontasse para ele. Isso e' uma propriedade do cliente
  HTTP/ABAP, nao do produto, e nunca implica validacao. O mapa imprime
  "generic (nao validado)" justamente para que a palavra "generic" nao
  seja lida como "funciona".
- `absent`: nada alcanca o par. E a fila de trabalho, nao um defeito.

Por que `absent` NAO reprova o build: exigir cobertura completa seria
exigir 20 conectores novos para o CI ficar verde, e o gate deixaria de
medir a coisa que importa. O gate reprova por INCOERENCIA (conector
registrado sem declaracao, produto fantasma, mecanismo fora das 9
dimensoes, doc desatualizado) e reporta as lacunas como saida.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTS_FILE = Path("data/sap_products.yaml")
COVERAGE_FILE = Path("data/connector_coverage.yaml")
MAP_DOC = Path("docs/COVERAGE_MAP.md")

# O enum traduz glifos da fonte. Ver o cabecalho de data/sap_products.yaml
# para a tabela completa e para o porque de `unknown` existir separado de
# `none`. `native` nao e' "melhor que supported": e' a forca maxima que a
# fonte marca com ✓✓.
LEVELS = frozenset({"native", "supported", "conditional", "limited", "none", "unknown"})

# Um nivel de capacidade que significa "o produto expoe isto". `none` e
# `unknown` NAO entram: uma celula desconhecida nao gera trabalho, gera
# primeiro uma verificacao de fonte, e mascarar as duas coisas num contador
# so seria pior.
AVAILABLE_LEVELS = frozenset({"native", "supported", "conditional", "limited"})

COVERAGE_DEDICATED = "dedicated"
COVERAGE_GENERIC = "generic"
COVERAGE_ABSENT = "absent"

_LEVEL_GLYPH = {
    "native": "✓✓",
    "supported": "✓",
    "conditional": "✓/cen",
    "limited": "lim",
    "none": "—",
    "unknown": "?",
}

_COVERAGE_GLYPH = {
    COVERAGE_DEDICATED: "D",
    COVERAGE_GENERIC: "G",
    COVERAGE_ABSENT: "·",
}

_KIND_TITLE = {
    "sap_product": "Produtos SAP (linhas da fonte)",
    "sap_middleware": "Middleware SAP (linha acrescentada por nos)",
    "non_sap": "Sistemas nao-SAP (linha acrescentada por nos)",
}

# O aviso de proveniencia mora AQUI, e nao no script gerador, porque o gate
# precisa renderizar exatamente a mesma string para comparar com o arquivo
# versionado. Se a frase vivesse em `scripts/`, o gate teria que importar
# um script para conferir um script, e a comparacao passaria a depender de
# duas copias da mesma verdade — que e' o modo exato de este arquivo
# envelhecer sem ninguem perceber.
GENERATED_NOTE = (
    "> **A coluna de capacidade e' afirmacao, nao medicao.** A matriz de produto foi\n"
    "> transcrita de uma fonte de referencia **sem citacao publicada e sem release SAP**\n"
    "> (ver o cabecalho de `data/sap_products.yaml`). A coluna de cobertura e' fato sobre\n"
    "> este repositorio e e' verificada por gate. Nao cite este mapa como fato de produto\n"
    "> SAP, e nao confunda as duas colunas."
)


class CoverageError(ValueError):
    """Dado de cobertura ausente, malformado ou incoerente."""


@dataclass(frozen=True)
class MechanismCell:
    level: str
    note: str = ""


@dataclass(frozen=True)
class Product:
    name: str
    kind: str
    mechanisms: dict[str, MechanismCell]
    note: str = ""


@dataclass(frozen=True)
class ConnectorCoverage:
    connector: str
    dedicated_to: tuple[str, ...]
    generic_for: tuple[str, ...]
    mechanisms: tuple[str, ...]
    note: str = ""


def _as_cell(raw: Any, product: str, mechanism: str) -> MechanismCell:
    """Aceita `supported` ou `{level: supported, note: ...}`.

    O dict existe por causa do `MDI` de SuccessFactors: e' um NOME DE
    MECANISMO numa coluna que guarda NIVEL DE SUPORTE. Achatar os dois no
    mesmo campo faria `MDI` e `✓` parecerem alternativa um do outro, e nao
    sao — sao coisas diferentes sobre o mesmo produto.
    """
    if isinstance(raw, str):
        level, note = raw, ""
    elif isinstance(raw, dict):
        level = raw.get("level")
        note = str(raw.get("note") or "")
    else:
        raise CoverageError(
            f"{product}/{mechanism}: valor {raw!r} nao e nivel de suporte nem {level, note}"
        )
    if level not in LEVELS:
        raise CoverageError(f"{product}/{mechanism}: nivel {level!r} fora do enum {sorted(LEVELS)}")
    return MechanismCell(level=level, note=note)


def load_products(root: Path = REPO_ROOT) -> tuple[tuple[Product, ...], tuple[str, ...]]:
    """Le `data/sap_products.yaml` e devolve (produtos, dimensoes)."""
    path = root / PRODUCTS_FILE
    if not path.exists():
        raise CoverageError(f"{PRODUCTS_FILE} nao existe")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mechanisms = tuple(data.get("mechanisms") or ())
    if not mechanisms:
        raise CoverageError(f"{PRODUCTS_FILE}: lista `mechanisms` vazia")
    linhas = data.get("products") or []
    if not linhas:
        raise CoverageError(f"{PRODUCTS_FILE}: lista `products` vazia")

    produtos: list[Product] = []
    vistos: set[str] = set()
    for linha in linhas:
        nome = linha.get("name")
        if not nome:
            raise CoverageError(f"{PRODUCTS_FILE}: linha sem `name`")
        if nome in vistos:
            raise CoverageError(f"{PRODUCTS_FILE}: produto duplicado {nome!r}")
        vistos.add(nome)
        kind = linha.get("kind")
        if kind not in _KIND_TITLE:
            raise CoverageError(f"{nome}: kind {kind!r} fora de {sorted(_KIND_TITLE)}")
        bruto = linha.get("mechanisms") or {}
        faltando = set(mechanisms) - set(bruto)
        if faltando:
            raise CoverageError(f"{nome}: sem mecanismo declarado para {sorted(faltando)}")
        extras = set(bruto) - set(mechanisms)
        if extras:
            raise CoverageError(
                f"{nome}: mecanismo fora das {len(mechanisms)} dimensoes {sorted(extras)}"
            )
        produtos.append(
            Product(
                name=nome,
                kind=kind,
                mechanisms={m: _as_cell(bruto[m], nome, m) for m in mechanisms},
                note=str(linha.get("note") or ""),
            )
        )
    return tuple(produtos), mechanisms


def load_coverage(root: Path = REPO_ROOT) -> tuple[ConnectorCoverage, ...]:
    """Le `data/connector_coverage.yaml`."""
    path = root / COVERAGE_FILE
    if not path.exists():
        raise CoverageError(f"{COVERAGE_FILE} nao existe")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    linhas = data.get("coverage") or []
    if not linhas:
        raise CoverageError(f"{COVERAGE_FILE}: lista `coverage` vazia")

    entradas: list[ConnectorCoverage] = []
    vistos: set[str] = set()
    for linha in linhas:
        nome = linha.get("connector")
        if not nome:
            raise CoverageError(f"{COVERAGE_FILE}: linha sem `connector`")
        if nome in vistos:
            raise CoverageError(f"{COVERAGE_FILE}: conector duplicado {nome!r}")
        vistos.add(nome)
        bruto = linha.get("dedicated_to")
        # `dedicated_to` aceita string ou lista. String isolada viraria
        # `tuple("Ariba")` = ('A','r','i',...) se eu convertesse sem
        # checar, e essa tupla de letras casaria com o nome de um produto
        # so por acaso — silenciosamente, num arquivo de dado.
        if isinstance(bruto, str):
            dedicated = (bruto,)
        else:
            dedicated = tuple(bruto or ())
        generic = tuple(linha.get("generic_for") or ())
        # Um conector pode ser escrito para mais de um produto (ariba cobre
        # Ariba E Business Network, e o proprio docstring diz as duas), e
        # isso e' dedicado, nao generico. Vazios nos dois, ao contrario,
        # e' conector orfao: o estado em que o mapa mente sobre o alcance
        # do repo sem nenhum sinal visivel.
        if set(dedicated) & set(generic):
            raise CoverageError(
                f"{nome}: {sorted(set(dedicated) & set(generic))} em `dedicated_to` e em `generic_for`"
            )
        if not dedicated and not generic:
            raise CoverageError(
                f"{nome}: sem `dedicated_to` e sem `generic_for` — conector orfao no mapa"
            )
        if not (linha.get("mechanisms") or ()):
            raise CoverageError(f"{nome}: `mechanisms` vazio — conector que nao exercita nenhum")
        entradas.append(
            ConnectorCoverage(
                connector=nome,
                dedicated_to=dedicated,
                generic_for=generic,
                mechanisms=tuple(linha["mechanisms"]),
                note=str(linha.get("note") or ""),
            )
        )
    return tuple(entradas)


def check_consistency(
    conectores: frozenset[str],
    products: tuple[Product, ...],
    coverage: tuple[ConnectorCoverage, ...],
) -> list[str]:
    """Incoerencias entre o registro, o dado e os produtos. Vazio = ok.

    Nao verifica se a cobertura e' boa — verifica se ela e' *coerente*.
    """
    problemas: list[str] = []
    declarados = {c.connector for c in coverage}
    sem_declaracao = sorted(conectores - declarados)
    if sem_declaracao:
        problemas.append(
            f"conectores registrados sem linha em {COVERAGE_FILE}: {sem_declaracao} "
            "(o mapa os mostra como sem cobertura — e invisivel, nao falso)"
        )
    orfaos = sorted(declarados - conectores)
    if orfaos:
        problemas.append(
            f"{COVERAGE_FILE} declara conectores fora do registro: {orfaos} "
            "(mapa affirmando cobertura que nao existe no codigo)"
        )

    nomes = {p.name for p in products}
    for entrada in coverage:
        for alvo in entrada.dedicated_to:
            if alvo not in nomes:
                problemas.append(
                    f"{entrada.connector}: dedicated_to {alvo!r} nao esta em {PRODUCTS_FILE}"
                )
        for alvo in entrada.generic_for:
            if alvo not in nomes:
                problemas.append(
                    f"{entrada.connector}: generic_for {alvo!r} nao esta em {PRODUCTS_FILE}"
                )
    return problemas


def _coverage_da_mecanism(
    product_name: str, mechanism: str, coverage: tuple[ConnectorCoverage, ...]
) -> str:
    dedicado = next(
        (
            c.connector
            for c in coverage
            if product_name in c.dedicated_to and mechanism in c.mechanisms
        ),
        None,
    )
    if dedicado:
        return COVERAGE_DEDICATED
    generico = next(
        (
            c.connector
            for c in coverage
            if product_name in c.generic_for and mechanism in c.mechanisms
        ),
        None,
    )
    return COVERAGE_GENERIC if generico else COVERAGE_ABSENT


@dataclass
class ProductCoverage:
    product: Product
    por_mecanismo: dict[str, str] = field(default_factory=dict)
    conectores_dedicados: tuple[str, ...] = ()
    conectores_genericos: tuple[str, ...] = ()
    disponiveis: tuple[str, ...] = ()
    lacunas: tuple[str, ...] = ()
    desconhecidos: tuple[str, ...] = ()

    @property
    def cobertura_pct(self) -> int:
        if not self.disponiveis:
            return 0
        return round(100 * (len(self.disponiveis) - len(self.lacunas)) / len(self.disponiveis))


def build_matrix(
    products: tuple[Product, ...],
    coverage: tuple[ConnectorCoverage, ...],
    mechanisms: tuple[str, ...] | None = None,
) -> list[ProductCoverage]:
    """Cruza capacidade declarada x cobertura do repo, produto a produto.

    Os dois eixos sao INDEPENDENTES de proposito. Uma celula pode ser
    `dedicated` (fato do repositorio) enquanto o nivel do produto e'
    `unknown` (nosso vazio de dado): o conector existe e fala com aquela
    linha, mas a fonte nao cobre middleware nem terceiros. Colapsar os
    dois num valor so faria um dos dois virar mentira.
    """
    if mechanisms is None:
        mechanisms = tuple(next(iter(products)).mechanisms) if products else ()

    linhas: list[ProductCoverage] = []
    for product in products:
        por_mec = {m: _coverage_da_mecanism(product.name, m, coverage) for m in mechanisms}
        disponiveis = tuple(
            m for m in mechanisms if product.mechanisms[m].level in AVAILABLE_LEVELS
        )
        lacunas = tuple(m for m in disponiveis if por_mec[m] == COVERAGE_ABSENT)
        desconhecidos = tuple(m for m in mechanisms if product.mechanisms[m].level == "unknown")
        linhas.append(
            ProductCoverage(
                product=product,
                por_mecanismo=por_mec,
                conectores_dedicados=tuple(
                    c.connector for c in coverage if product.name in c.dedicated_to
                ),
                conectores_genericos=tuple(
                    c.connector for c in coverage if product.name in c.generic_for
                ),
                disponiveis=disponiveis,
                lacunas=lacunas,
                desconhecidos=desconhecidos,
            )
        )
    return linhas


def render_markdown(
    linhas: list[ProductCoverage],
    mechanisms: tuple[str, ...],
    labels: dict[str, str] | None = None,
    generated_note: str = "",
) -> str:
    """Mapa em Markdown. Determinismo e' requisito: o gate compara texto."""
    labels = labels or {}
    cols = [labels.get(m, m) for m in mechanisms]
    out: list[str] = []
    out.append("# Mapa de cobertura: produto x mecanismo (DA-58)")
    out.append("")
    out.append("<!-- Arquivo GERADO por scripts/coverage_map.py --write. Nao edite a mao:")
    out.append("     o gate `connector_coverage` reprova se este texto divergir do calculado. -->")
    out.append("")
    if generated_note:
        out.append(generated_note)
        out.append("")
    out.append("## Como ler")
    out.append("")
    out.append("Dois eixos independentes, e nao uma tabela de ticks unica:")
    out.append("")
    out.append("- **Capacidade** (`Tabela A`) e' afirmacao sobre o PRODUTO SAP. Vem de")
    out.append("  `data/sap_products.yaml`, transcrita de uma fonte de referencia **sem")
    out.append("  citacao publicada e sem release SAP**. Nao e' medicao nossa.")
    out.append("- **Cobertura** (`Tabela B`) e' fato sobre o REPOSITORIO: existe codigo")
    out.append("  que fala com este par. E' verificavel, e por isso a unica coluna em que")
    out.append("  este projeto pode afirmar sem ressalva.")
    out.append("")
    out.append("Legenda de cobertura: `D` dedicado (conector feito para o produto) |")
    out.append("`G` generico (cliente de mecanismo, **nao validado** contra este produto) |")
    out.append("`·` sem cobertura.")
    out.append("")
    out.append("`G` nao e' sinonimo de 'funciona'. O `ODataConnector` e' `G` para 15")
    out.append("produtos e nao foi validado contra nenhum deles — `docs/ARCHITECTURE.md` e'")
    out.append("quem diz o que foi validado contra sistema real.")
    out.append("")

    header = "| Produto | " + " | ".join(cols) + " |"
    sep = "|" + "---|" * (len(cols) + 1)

    out.append("## Tabela A — capacidade por produto (afirmacao da fonte)")
    out.append("")
    out.append(header)
    out.append(sep)
    for linha in linhas:
        cells = [_LEVEL_GLYPH[linha.product.mechanisms[m].level] for m in mechanisms]
        out.append(f"| {linha.product.name} | " + " | ".join(cells) + " |")
    out.append("")

    out.append("## Tabela B — cobertura do repositorio por par (fato do codigo)")
    out.append("")
    out.append(header)
    out.append(sep)
    for linha in linhas:
        cells = [_COVERAGE_GLYPH[linha.por_mecanismo[m]] for m in mechanisms]
        out.append(f"| {linha.product.name} | " + " | ".join(cells) + " |")
    out.append("")

    out.append("## Resumo por produto")
    out.append("")
    out.append(
        "| Produto | Categoria | Conectores dedicados | Genericos | Disponiveis | Cobertos | Lacunas |"
    )
    out.append("|---|---|---|---|---|---|---|")
    for linha in linhas:
        cobertos = len(linha.disponiveis) - len(linha.lacunas)
        lacunas = ", ".join(labels.get(m, m) for m in linha.lacunas) or "—"
        out.append(
            f"| {linha.product.name} | {linha.product.kind} | "
            f"{', '.join(linha.conectores_dedicados) or '—'} | "
            f"{', '.join(linha.conectores_genericos) or '—'} | "
            f"{len(linha.disponiveis)} | {cobertos} | {lacunas} |"
        )
    out.append("")

    sem_cobertura = [l for l in linhas if l.lacunas]
    out.append("## Filas com lacuna")
    out.append("")
    if not sem_cobertura:
        out.append("Nenhuma: todos os mecanismos declarados tem cobertura.")
    else:
        out.append(
            "Os mecanismos listados sao os que o produto expoe e que nada no repositorio "
            "alcanca. E' a fila de trabalho, e nao um defeito — por isso o gate "
            "`connector_coverage` reprova por incoerencia, nunca por falta aqui."
        )
        out.append("")
        out.append("| Produto | Conectores dedicados | Mecanismos sem cobertura |")
        out.append("|---|---|---|")
        for linha in sem_cobertura:
            out.append(
                f"| {linha.product.name} | {', '.join(linha.conectores_dedicados) or '—'} | "
                + ", ".join(labels.get(m, m) for m in linha.lacunas)
                + " |"
            )
    out.append("")

    desconhecidos = [l for l in linhas if l.desconhecidos]
    if desconhecidos:
        out.append("## Linhas sem dado de capacidade")
        out.append("")
        out.append(
            "Estas linhas existem porque o repo tem conector, mas a fonte nao as cobre. "
            "Todos os mecanismos marcados `?` na Tabela A sao **desconhecidos, nao negativos**: "
            "converter `?` em `—` aqui seria inventar uma negativa. Preencher isso e' trabalho de "
            "fonte, nao de codigo."
        )
        out.append("")
        for linha in desconhecidos:
            out.append(f"- **{linha.product.name}** ({linha.product.kind})")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def load_all(root: Path = REPO_ROOT):
    """Atalho: produtos, dimensoes e cobertura, ja validados entre si."""
    products, mechanisms = load_products(root)
    coverage = load_coverage(root)
    labels = _load_labels(root)
    return products, mechanisms, coverage, labels


def _load_labels(root: Path) -> dict[str, str]:
    path = root / PRODUCTS_FILE
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    labels = data.get("mechanism_labels") or {}
    return {str(k): str(v) for k, v in labels.items()}


def gaps_summary(linhas: list[ProductCoverage]) -> dict[str, int]:
    contagem: dict[str, int] = {}
    for linha in linhas:
        for m in linha.lacunas:
            contagem[m] = contagem.get(m, 0) + 1
    return dict(sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0])))
