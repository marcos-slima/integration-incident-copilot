# Arquitetura do produto IIC

O IIC é um consumidor da plataforma, não o repositório do core.

| Camada | Local e responsabilidade |
|---|---|
| Entrada Python | `iic_copilot/main.py`: configura UI/cookie e carrega `app.sdk.asgi_app()` |
| UI do produto | `frontend/src`, transportes de integração e textos do IIC |
| Contrato público | `app.sdk`, HTTP e índice público de `@copilot/core-ui` |
| Implementação compartilhada | copilot-platform: grafo, gateway, RAG, evidências, auth e conectores |
| Operações comuns | bundle copilot-operations: Alembic, dashboards e validador |
| Entrega do produto | Dockerfile, docker-compose.yml, deploy/ e `.github/workflows/ci.yml` |

`shared-libraries.json`, `uv.lock` e `frontend/package-lock.json` fixam as
versões/hashes. Instalar bibliotecas não exige publicar nova release do produto.
O core não importa os produtos; IIC não importa FISC. Não reintroduzir árvores
`app/` ou `packages/core/` neste repositório.

A UI compilada entra no wheel em `iic_copilot/_ui`. O Dockerfile instala wheels,
preserva os targets `app`/`final`, TLS AMQP e preload opcional de modelos.
O bundle operacional mantém `/app/alembic.ini` e `/app/alembic/` na imagem;
no checkout, use `.operations/` conforme [deploy](DEPLOY.md).

Contratos HTTP/SDK 1.0 e plugin 1.1 são distintos da versão dos pacotes.
Leia os [contratos do core](https://github.com/marcos-slima/copilot-platform/blob/main/packages/core/docs/CONTRACTS.md),
a [arquitetura interna](https://github.com/marcos-slima/copilot-platform/blob/main/docs/ARCHITECTURE.md)
e as [DAs](https://github.com/marcos-slima/copilot-platform/blob/main/docs/DECISOES_DE_ARQUITETURA.md).
A UI do IIC é verificada contra o schema de transportes do SDK.

Fluxos e evidências de implantação do antigo monorepo estão no
[histórico](history/README.md); não são validação da nova imagem em Kyma/produção.
