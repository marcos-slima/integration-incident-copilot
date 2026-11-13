# Publicação e consumo do `copilot-core`

Este procedimento prepara a separação física do core sem depender do workspace
`uv` nem de um registry específico. A publicação real continua sendo uma ação
do ambiente de release e exige escolher um registry privado.

## Pré-condições

- `packages/core/pyproject.toml` define `copilot-core` e a versão do pacote.
- O contrato público está em [`CONTRACTS.md`](CONTRACTS.md).
- O gate de fronteiras está em `architecture/boundaries.toml`.
- O teste de isolamento constrói e instala os wheels fora do checkout:
  `scripts/test_product_isolation.py`.

## Validar o artefato local

Na raiz do repositório:

```bash
rm -rf dist/core-release
uv build --package copilot-core --wheel --out-dir dist/core-release
uv run python scripts/test_product_isolation.py
```

O teste deve provar, sem importar o código-fonte do IIC, que o core inicializa
sem produtos e que o DF-e executa sua suíte usando o wheel instalado. O diretório
`dist/` é artefato local e não deve ser commitado.

## Consumidor externo (após escolher o registry)

O projeto consumidor deve declarar somente uma versão publicada, por exemplo:

```toml
[project]
dependencies = ["copilot-core==1.4.0"]
```

Configure o índice privado por mecanismo de CI/ambiente (token fora do
repositório), execute `uv lock --upgrade-package copilot-core` e confirme que o
lockfile aponta para o artefato publicado. O produto deve passar o gate de
imports e seus testes sem `tool.uv.sources.copilot-core = { workspace = true }`.

## Critérios de aceite da separação

1. O wheel publicado possui `direct_url.json` sem `dir_info.editable`.
2. O DF-e instala o core do registry, não do checkout nem de `file://`.
3. O core compila e testa sem instalar IIC ou DF-e.
4. O contrato HTTP/plugin/SDK permanece compatível com a versão declarada.
5. O pipeline autentica no registry por segredo do ambiente e não grava token,
   URL privada ou credencial em logs, `.env`, imagem ou documentação.

O merge da P2 e a escolha do registry são pré-requisitos para executar a
publicação real; este documento não publica nem envia artefatos.
