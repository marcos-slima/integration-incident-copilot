# IIC como consumidor

Core e UI vêm de releases de copilot-platform. O código app e a suíte do core
foram preservados no histórico da plataforma; não são copiados neste produto.
A interface iic_copilot.main:app preserva as rotas do SDK e instala sua própria UI.
A versão de produto declarada permanece 1.4.0 como candidato de código:
esta mudança não publica release IIC nem resolve automaticamente as tags 2.x históricas.
As versões compartilhadas são independentes do número do produto.

Gates de arquitetura/quality do core são executados upstream. Este repositório
valida o shell instalado, o contrato de transportes na UI, HTTP e imagem.
A desativação de pipelines antigos decorre dessa transferência de propriedade,
não de remover testes funcionais do core. Leia o inventário da plataforma.

## Instalação e operação

Use uv sync --locked, npm ci --prefix frontend, npm run build --prefix frontend,
IIC_BUILD_REQUIRE_UI=1 uv build --wheel --no-sources e instale o wheel antes dos
checks python -I. O servidor do produto é iic_copilot.main:app.
Para desenvolvimento com UI fora do wheel, configure UI_DIST_DIR=frontend/dist.

shared-libraries.json fixa as versões e hashes das três dependências. Baixe
operações com python scripts/fetch_operations.py .operations. Execute migrations
explicitamente com uv run alembic -c .operations/alembic.ini upgrade head;
Compose monta dashboards de .operations/deploy/grafana/dashboards.
A imagem preserva /app/alembic.ini e /app/alembic/ para comandos já utilizados.
Não execute migrations apenas para construir ou testar o produto.

A suíte funcional extensa do core, migrations/dashboards e RAG estão na CI da
plataforma. A CI IIC testa o wheel instalado, contrato de transportes, HTTP,
UI e imagem. PRELOAD_MODELS=0 no smoke permite verificar empacotamento sem
Hugging Face; o padrão da imagem continua PRELOAD_MODELS=1, com teste offline.
O smoke não é validação de LLM, SAP ou ambiente Kyma real.
