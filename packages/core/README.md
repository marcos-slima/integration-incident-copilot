# Copilot Core

Pacote Python `copilot-core` 1.4.0. A implementação reside em `src/app`, mantendo
os imports históricos `app.*`. Produtos importam exclusivamente `app.sdk`;
o core não depende dos pacotes IIC ou DF-e. A biblioteca neutra de UI é
`@copilot/core-ui`, em `../core-ui`.

## Construção e consumo

```bash
uv build --wheel
```

O wheel inclui Python, templates, arquivos de idioma, ontologia e as skills de
`data/skills`. Não requer fontes de produto para ser construído. No monorepo,
`uv sync --locked --all-packages` instala os três pacotes. Fora dele, instale
os wheels de core e produto no mesmo ambiente com uv.

O pacote fornece serviços de diagnóstico, autenticação e infraestrutura de
conectores. `iic_copilot.main` e `dfe_copilot.main` são entradas de produto.
`app.main` continua disponível para compatibilidade. A configuração externa
continua em variáveis de ambiente/`.env` do diretório de execução. Arquivos
operacionais de dados usam `COPILOT_HOME` quando definido, a raiz do monorepo
quando descoberta, e o diretório atual em uma instalação independente.

## Contratos

Veja `docs/CONTRACTS.md`. Versões de contrato são independentes da versão do
pacote. A suíte completa do core está em `../../tests`; sua execução no
workspace é `uv run pytest tests/ -m "not integration"` a partir da raiz.
O namespace Python é exclusivo deste pacote: não instale versões antigas do
IIC que também distribuíam `app` no mesmo ambiente de produção. No workspace,
`uv sync --locked` atualiza a distribuição raiz e evita esse conflito.
A prova de distribuição é `uv run python scripts/test_product_isolation.py`.
