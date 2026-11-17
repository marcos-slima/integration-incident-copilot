# Implantação do IIC

Para execução nativa e instalação do wheel, siga [primeira execução](GETTING_STARTED.md).
O Dockerfile e o Compose deste produto não precisam das fontes do core ou do FISC.

## Preparar configuração e operações

Copie `.env.example` para `.env`, ajuste URLs/modelos e configure credenciais
fora do Git. O Compose exige `POSTGRES_PASSWORD`, `GRAFANA_PASSWORD` e
`NEO4J_PASSWORD` na interpolação, mesmo quando os respectivos profiles não são
iniciados. Não use senhas de exemplo ou de produção em testes.

Após sincronizar as dependências como no quickstart:

~~~bash
uv run --no-sync python scripts/fetch_operations.py .operations
docker compose --env-file .env config --quiet
~~~

O fetch confere SHA-256 antes de extrair o bundle fixado em shared-libraries.json.
Dashboards são montados de `.operations/deploy/grafana/dashboards`.
A cadeia canônica de migrations vive na plataforma, não neste checkout.

## Imagem e serviços

~~~bash
docker compose --env-file .env up -d --build api
~~~

O Compose inicia as dependências declaradas para `api`; não representa ativação
de todos os profiles. Ollama é um serviço do host por padrão; o profile
`container-ollama` é opt-in. Qdrant e os modelos precisam corresponder ao ambiente.
Não foi feita validação nova de inferência, SAP ou deployment de produção nesta migração.

O build padrão `PRELOAD_MODELS=1` incorpora e verifica caches offline dos modelos.
Para verificar apenas empacotamento, use:

~~~bash
docker build --build-arg PRELOAD_MODELS=0 -t iic-copilot:ci .
uv run --no-sync python -I scripts/smoke_image.py iic-copilot:ci
~~~

Esse smoke verifica auth, UI, pacotes, operações, usuário não-root e TLS; não
faz diagnóstico por LLM real. Não altera serviços do deployment.

## Migrations e outros ambientes

Migrations são uma ação explícita sobre banco autorizado, após confirmar destino,
compatibilidade e backup. No checkout: `uv run --no-sync alembic -c .operations/alembic.ini upgrade head`.
Na imagem, a configuração continua em `/app/alembic.ini`; o build não executa migrations.
Perfis, worker e reporter são definidos em docker-compose.yml.

Manifests e overlays continuam em [deploy/kyma](../deploy/kyma/README.md) e
[deploy/local](../deploy/local/README.md). Seus registros antigos de k3d/Kyma
são históricos; valide a imagem e a configuração no ambiente de destino antes de uso.
