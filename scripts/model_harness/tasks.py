"""Tarefas de refactoring - cada uma com contrato explicito e solucao.

Uma tarefa so vale se TRES propriedades forem verdadeiras, e as tres sao
travadas por `tests/test_model_harness.py` (nao por confianca):

1. FALHA no estado inicial -> a tarefa tem sinal. Se o teste oculto ja
   passa na fixture crua, o modelo pode nao fazer nada e "vencer".
2. PASSA com a solucao de referencia -> a tarefa e verificavel. Se nem a
   solucao escrita a mao passa, o teste oculto esta errado, nao o modelo.
3. O teste oculto importa coisa que EXISTE na fixture -> sem isso, o
   veredito e "ModuleNotFoundError", nunca a capacidade do modelo.

Cada `instruction` NOMEIA o contrato (ex: "crie a funcao `formatar_linha`")
para que o teste oculto possa cobrar a estrutura sem acoplar a uma
implementacao que o modelo nao foi instruido a seguir.

NOTA DE SEGURANCA (2026-10-11): este arquivo contem as fixtures E as
solucoes. Um modelo que ESCAPE do sandbox pode acha-lo (via glob/grep) e
"corrigir" as fixtures aqui dentro - ja aconteceu no primeiro smoke. O
harness hoje roda com `--standalone` + `PWD` do sandbox justamente para
isso, e `tests/test_model_harness.py` reprova se uma fixture perder o
sinal. Se este arquivo for editado por um modelo, rode o gate de testes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from textwrap import dedent


def _d(text: str) -> str:
    """Normaliza um bloco de codigo escrito inline (tira indentacao + lead)."""
    return dedent(text).lstrip("\n")


@dataclass(frozen=True)
class Task:
    """Uma unidade de medida: fixture + instrucao + teste oculto + solucao."""

    id: str
    title: str
    instruction: str
    work: dict[str, str]
    hidden: str
    solution: dict[str, str]
    frozen: tuple[str, ...] = field(default_factory=tuple)
    timeout_s: int = 240


TAREFAS: dict[str, Task] = {}


TAREFAS["extrair_helper"] = Task(
    id="extrair_helper",
    title="Extrair helper duplicado preservando a saida",
    instruction=(
        "Em `relatorio.py` ha duas funcoes com a MESMA logica de formatacao "
        "de linha repetida. Crie uma funcao `formatar_linha(nome, valor)` que "
        "devolve exatamente a linha formatada usada hoje e reescreva as duas "
        "funcoes para usar essa helper. A saida de `relatorio_vendas` e "
        "`relatorio_estoque` nao pode mudar em nenhum caractere."
    ),
    work={
        "relatorio.py": _d(
            """
            def relatorio_vendas(itens):
                linhas = []
                for nome, valor in itens:
                    linhas.append(f"{nome.ljust(20)} R$ {valor:8.2f}")
                total = sum(valor for _, valor in itens)
                linhas.append(f"{'TOTAL'.ljust(20)} R$ {total:8.2f}")
                return "\\n".join(linhas)


            def relatorio_estoque(itens):
                linhas = []
                for nome, valor in itens:
                    linhas.append(f"{nome.ljust(20)} R$ {valor:8.2f}")
                total = sum(valor for _, valor in itens)
                linhas.append(f"{'TOTAL'.ljust(20)} R$ {total:8.2f}")
                return "\\n".join(linhas)
            """
        ),
    },
    hidden=_d(
        """
        import relatorio


        def test_formatar_linha_existe_e_formata():
            assert hasattr(relatorio, "formatar_linha")
            assert relatorio.formatar_linha("caneta", 3.5) == f"{'caneta'.ljust(20)} R$ {3.5:8.2f}"


        def test_vendas_inalterado():
            out = relatorio.relatorio_vendas([("caneta", 3.5), ("papel", 10.0)])
            esperado = "\\n".join([
                f"{'caneta'.ljust(20)} R$ {3.5:8.2f}",
                f"{'papel'.ljust(20)} R$ {10.0:8.2f}",
                f"{'TOTAL'.ljust(20)} R$ {13.5:8.2f}",
            ])
            assert out == esperado


        def test_estoque_inalterado():
            out = relatorio.relatorio_estoque([("parafuso", 0.25), ("porca", 1.5)])
            esperado = "\\n".join([
                f"{'parafuso'.ljust(20)} R$ {0.25:8.2f}",
                f"{'porca'.ljust(20)} R$ {1.5:8.2f}",
                f"{'TOTAL'.ljust(20)} R$ {1.75:8.2f}",
            ])
            assert out == esperado
        """
    ),
    solution={
        "relatorio.py": _d(
            """
            def formatar_linha(nome, valor):
                return f"{nome.ljust(20)} R$ {valor:8.2f}"


            def relatorio_vendas(itens):
                linhas = [formatar_linha(nome, valor) for nome, valor in itens]
                total = sum(valor for _, valor in itens)
                linhas.append(formatar_linha("TOTAL", total))
                return "\\n".join(linhas)


            def relatorio_estoque(itens):
                linhas = [formatar_linha(nome, valor) for nome, valor in itens]
                total = sum(valor for _, valor in itens)
                linhas.append(formatar_linha("TOTAL", total))
                return "\\n".join(linhas)
            """
        ),
    },
)


TAREFAS["dispatch_dict"] = Task(
    id="dispatch_dict",
    title="Trocar cadeia if/elif por dict de handlers",
    instruction=(
        "Em `processar.py`, substitua a cadeia `if/elif` de `processar` por um "
        "dicionario de modulo chamado `HANDLERS` que mapeia o nome do tipo "
        "(`soma`, `subtracao`, `multiplicacao`) para a funcao correspondente. "
        "`processar` deve consultar `HANDLERS` e continuar levantando "
        "`ValueError` para tipo desconhecido. O resultado dos tipos validos nao "
        "pode mudar."
    ),
    work={
        "processar.py": _d(
            """
            def _somar(a, b):
                return a + b


            def _subtrair(a, b):
                return a - b


            def _multiplicar(a, b):
                return a * b


            def processar(tipo, a, b):
                if tipo == "soma":
                    return _somar(a, b)
                elif tipo == "subtracao":
                    return _subtrair(a, b)
                elif tipo == "multiplicacao":
                    return _multiplicar(a, b)
                else:
                    raise ValueError(f"tipo desconhecido: {tipo}")
            """
        ),
    },
    hidden=_d(
        """
        import pytest

        import processar


        def test_handlers_dict():
            assert isinstance(processar.HANDLERS, dict)
            assert {"soma", "subtracao", "multiplicacao"} <= set(processar.HANDLERS)


        def test_comportamento():
            assert processar.processar("soma", 2, 3) == 5
            assert processar.processar("subtracao", 5, 2) == 3
            assert processar.processar("multiplicacao", 4, 3) == 12


        def test_desconhecido():
            with pytest.raises(ValueError):
                processar.processar("potencia", 2, 3)
        """
    ),
    solution={
        "processar.py": _d(
            """
            def _somar(a, b):
                return a + b


            def _subtrair(a, b):
                return a - b


            def _multiplicar(a, b):
                return a * b


            HANDLERS = {
                "soma": _somar,
                "subtracao": _subtrair,
                "multiplicacao": _multiplicar,
            }


            def processar(tipo, a, b):
                try:
                    handler = HANDLERS[tipo]
                except KeyError:
                    raise ValueError(f"tipo desconhecido: {tipo}") from None
                return handler(a, b)
            """
        ),
    },
)


TAREFAS["bug_mediana"] = Task(
    id="bug_mediana",
    title="Corrigir mediana para lista de tamanho par",
    instruction=(
        "`estatistica.py::mediana` esta errada para listas com quantidade PAR "
        "de elementos: hoje devolve o elemento central superior em vez da media "
        "dos dois centrais. Corrija, mantendo intacto o resultado para "
        "quantidade IMPAR e para listas de um elemento."
    ),
    work={
        "estatistica.py": _d(
            '''
            def mediana(valores):
                """Retorna a mediana de uma lista nao vazia de numeros."""
                ordenados = sorted(valores)
                return ordenados[len(ordenados) // 2]
            '''
        ),
    },
    hidden=_d(
        """
        from estatistica import mediana


        def test_impar():
            assert mediana([3, 1, 2]) == 2
            assert mediana([7]) == 7


        def test_par():
            assert mediana([1, 2, 3, 4]) == 2.5
            assert mediana([4, 1, 3, 2]) == 2.5


        def test_par_ordem_qualquer():
            assert mediana([10, 2, 8, 4]) == 6.0
        """
    ),
    solution={
        "estatistica.py": _d(
            '''
            def mediana(valores):
                """Retorna a mediana de uma lista nao vazia de numeros."""
                ordenados = sorted(valores)
                n = len(ordenados)
                meio = n // 2
                if n % 2 == 1:
                    return ordenados[meio]
                return (ordenados[meio - 1] + ordenados[meio]) / 2
            '''
        ),
    },
)


TAREFAS["tipos_docstrings"] = Task(
    id="tipos_docstrings",
    title="Adicionar type hints e docstrings Google sem mudar comportamento",
    instruction=(
        "Em `geometria.py`, adicione anotacoes de tipo (parametros e retorno, "
        "todos `float`) e docstrings no formato Google (com secoes `Args:` e "
        "`Returns:`) as duas funcoes. Nao altere o comportamento nem o valor "
        "retornado."
    ),
    work={
        "geometria.py": _d(
            """
            def area_retangulo(base, altura):
                return base * altura


            def area_circulo(raio):
                return 3.14159 * raio ** 2
            """
        ),
    },
    hidden=_d(
        """
        import inspect

        import geometria


        def test_anotacoes_e_docstrings():
            for nome in ("area_retangulo", "area_circulo"):
                fn = getattr(geometria, nome)
                sig = inspect.signature(fn)
                for p in sig.parameters.values():
                    assert p.annotation is not inspect.Parameter.empty
                assert sig.return_annotation is not inspect.Signature.empty
                doc = fn.__doc__ or ""
                assert "Args:" in doc
                assert "Returns:" in doc


        def test_comportamento():
            assert geometria.area_retangulo(3.0, 4.0) == 12.0
            assert round(geometria.area_circulo(1.0), 5) == 3.14159
        """
    ),
    solution={
        "geometria.py": _d(
            '''
            def area_retangulo(base: float, altura: float) -> float:
                """Calcula a area de um retangulo.

                Args:
                    base: A medida da base.
                    altura: A medida da altura.

                Returns:
                    A area do retangulo.
                """
                return base * altura


            def area_circulo(raio: float) -> float:
                """Calcula a area de um circulo.

                Args:
                    raio: O raio do circulo.

                Returns:
                    A area do circulo.
                """
                return 3.14159 * raio ** 2
            '''
        ),
    },
)


TAREFAS["dataclass"] = Task(
    id="dataclass",
    title="Converter classe manual em @dataclass mantendo a API",
    instruction=(
        "Converta a classe `Cliente` de `cliente.py` em um `@dataclass` com os "
        "mesmos campos (`nome`, `email`) e a mesma igualdade por valor. O "
        "construtor posicional `Cliente(nome, email)` deve continuar valendo."
    ),
    work={
        "cliente.py": _d(
            """
            class Cliente:
                def __init__(self, nome, email):
                    self.nome = nome
                    self.email = email

                def __eq__(self, other):
                    return isinstance(other, Cliente) and (self.nome, self.email) == (
                        other.nome,
                        other.email,
                    )
            """
        ),
    },
    hidden=_d(
        """
        import dataclasses

        from cliente import Cliente


        def test_dataclass():
            assert dataclasses.is_dataclass(Cliente)


        def test_posicional_e_campos():
            c = Cliente("ana", "ana@ex.com")
            assert c.nome == "ana"
            assert c.email == "ana@ex.com"


        def test_igualdade_por_valor():
            assert Cliente("ana", "a@x") == Cliente("ana", "a@x")
            assert Cliente("ana", "a@x") != Cliente("bia", "a@x")
        """
    ),
    solution={
        "cliente.py": _d(
            """
            from dataclasses import dataclass


            @dataclass
            class Cliente:
                nome: str
                email: str
            """
        ),
    },
)


TAREFAS["precisao_frozen"] = Task(
    id="precisao_frozen",
    title="Editar so o alvo (mede sobre-edicao)",
    instruction=(
        "Em `alvo.py`, faca `normalizar` tambem colapsar espacos internos "
        "repetidos em um unico espaco (dica: `texto.split()` + `' '.join`), "
        "mantendo o `strip` das bordas. IMPORTANTE: nao modifique nenhum outro "
        "arquivo do diretorio."
    ),
    work={
        "alvo.py": _d(
            """
            def normalizar(texto):
                return texto.strip()
            """
        ),
        "intocavel.py": _d(
            """
            LIMITE = 10


            def dentro_do_limite(valor):
                return valor <= LIMITE
            """
        ),
    },
    hidden=_d(
        """
        import alvo


        def test_colapsa_espacos():
            assert alvo.normalizar("  a   b  ") == "a b"
            assert alvo.normalizar("a b") == "a b"
            assert alvo.normalizar("x") == "x"
            assert alvo.normalizar("  ") == ""
        """
    ),
    solution={
        "alvo.py": _d(
            """
            def normalizar(texto):
                return " ".join(texto.split())
            """
        ),
    },
    frozen=("intocavel.py",),
)


def all_tasks() -> list[Task]:
    """Todas as tarefas, em ordem estavel (por id)."""
    return [TAREFAS[k] for k in sorted(TAREFAS)]


def get_tasks(ids: list[str] | None) -> list[Task]:
    """Filtra por ids; `None`/vazio devolve todas."""
    if not ids:
        return all_tasks()
    faltando = [i for i in ids if i not in TAREFAS]
    if faltando:
        raise KeyError(f"tarefa(s) inexistente(s): {', '.join(faltando)}")
    return [TAREFAS[i] for i in ids]
