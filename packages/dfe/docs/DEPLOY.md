# DF-e Copilot — imagem, execução e CI próprios (P2)

## Artefatos

O alvo `dfe` do Dockerfile da raiz produz a imagem do produto. Ele compartilha
os estágios de dependências e runtime com o IIC, sem copiar o shell IIC nem
seus documentos de exemplo. O alvo padrão `final` continua sendo o IIC.

Core e DF-e são instalados como wheels. O pacote fiscal inclui schemas XSD,
manifesto, catálogo cStat, regra curada, NT versionada e o bundle de UI.
`DFE_BUILD_REQUIRE_UI=1` faz o build falhar se a UI não tiver sido construída;
um wheel somente de backend continua possível para testes e desenvolvimento.
O MOC opcional baixado localmente não é incorporado à imagem.

Na raiz do repositório:

```bash
docker build --target dfe --build-arg PRELOAD_MODELS=0 -t dfe-copilot:local .
```

`PRELOAD_MODELS=0` evita baixar os modelos no build; o runtime pode obtê-los
sob demanda. Para incorporar os modelos, use `PRELOAD_MODELS=1`, que exige
acesso às fontes de modelos. Esse segundo caminho não foi repetido na
validação local da P2.

## Primeiro acesso e coexistência

Copie `packages/dfe/.env.example` para `packages/dfe/.env`. Esse arquivo é
local, ignorado pelo Git, e não é copiado para a imagem. O Compose não monta
o `.env` do IIC nem reutiliza automaticamente suas credenciais.

Gere o hash da senha no terminal, sem colocá-la no histórico:

```bash
uv run python -c 'from getpass import getpass; from app.auth import hash_password; print(hash_password(getpass("Senha do operador DF-e: ")))'
```

Configure no arquivo local:

```dotenv
DFE_WEB_UI_USERS="admin:<hash-gerado-no-terminal>"
DFE_SESSION_SECRET="<segredo-gerado-no-terminal>"
```

Gere o segredo com `uv run python -c 'import secrets; print(secrets.token_urlsafe(32))'`.
Não compartilhe senha, hash nem segredo. O usuário bootstrap entra pelo login
normal; não precisa da ativação por e-mail/telefone. A ativação é para contas
previamente cadastradas no banco, que este Compose local não configura.

Inicie usando o arquivo de ambiente do produto explicitamente:

```bash
docker compose --env-file packages/dfe/.env -f packages/dfe/compose.yaml up -d --build
docker compose --env-file packages/dfe/.env -f packages/dfe/compose.yaml ps
```

Abra http://localhost:8002. A porta é publicada apenas em loopback e pode ser
alterada por `DFE_PORT`. O projeto Compose `dfe-copilot` tem rede e volume de
Qdrant próprios; o Qdrant não publica porta no host. IIC em 8000 e DF-e nativo
em 8001 podem continuar executando. Cookies padrão distintos evitam colisão
no mesmo navegador: IIC `iic_session`, DF-e nativo `dfe_session`, Compose
`dfe_container_session`. Outras instâncias podem definir `SESSION_COOKIE_NAME`
ou `DFE_SESSION_COOKIE_NAME` conforme seu modo de execução.

O Compose usa o Ollama do host por `DFE_OLLAMA_HOST`. A URL precisa ser
alcançável do contêiner; a acessibilidade do modelo não é inferida pelo
healthcheck. `/health` verifica a aplicação; `/ready` verifica os serviços
configurados. Configure `DFE_LLM_MODEL` e `DFE_EMBEDDING_MODEL` para os modelos
provisionados no ambiente.

O Qdrant do produto começa vazio: a base do IIC não é importada. Depois de
provisionar Ollama e modelos, execute a ingestão idempotente do corpus fiscal:

```bash
docker compose --env-file packages/dfe/.env -f packages/dfe/compose.yaml exec dfe python -m dfe_copilot.kb ingest
```

O corpus distribuído inclui apenas as fontes versionadas. O MOC opcional e a
base já existente no ambiente nativo não são migrados automaticamente.

### Usar uma base Qdrant fiscal já existente (somente leitura operacional)

Quando a base fiscal já foi ingerida por outro serviço, use o Compose de
coexistência, e não `compose.yaml`:

```bash
docker compose --env-file packages/dfe/.env \
  -f packages/dfe/compose.existing.yaml up -d
```

Esse arquivo não declara serviço, volume ou porta de Qdrant. A aplicação entra
somente na rede externa indicada por `DFE_EXISTING_NETWORK` e recebe
`DFE_EXISTING_QDRANT_URL` apontando para o serviço existente. Declare também o
mesmo `DFE_EXISTING_EMBEDDING_MODEL` usado pela coleção. Os caches locais de
BM25 e do reranker são montados como volumes somente leitura; não copie o
`.env` da origem nem credenciais para a imagem. O modo de validação calcula a
contagem e o SHA-256 canônico da coleção antes/depois e falha se houver
alteração. Ele não executa ingestão, reset ou migração.

Em 2026-11-13, esse modo foi exercitado contra a coleção `dfe_kb` existente:
310 pontos, SHA-256
`842c82b9442cbf171bef529a43e67f0d0ad0f697bd049801feebc277514868a0` antes e
depois; dois casos sintéticos revisados foram diagnosticados pelo Ollama real.
O SHA identifica o estado observado nessa execução, não a origem histórica da
ingestão nem a identidade dos embeddings anteriores.

### Ativar retomada de revisão humana

A revisão humana que aprova, edita ou rejeita um diagnóstico pausado exige um
checkpointer persistente. Ela é opt-in e usa um Compose separado, para não
adicionar PostgreSQL ao runtime fiscal padrão:

```bash
export DFE_POSTGRES_PASSWORD="$(openssl rand -hex 32)"
export DFE_LLM_CREDENTIALS_MASTER_KEY="$(uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
docker compose --env-file packages/dfe/.env \
  -f packages/dfe/compose.review.yaml up -d
```

Esse arquivo ativa `CHECKPOINTING_ENABLED=true`, exige a chave Fernet
`DFE_LLM_CREDENTIALS_MASTER_KEY` para cifragem em repouso e cria o volume
`dfe_review_postgres` e só libera o DF-e depois do healthcheck do PostgreSQL.
Não use senha padrão nem a inclua em `.env` versionado. Sem esse Compose (ou
um PostgreSQL externo equivalente), a API pode sinalizar `pending_review`, mas
`can_resume=false` e não apresenta uma ação executável de aprovação.

Para integrações máquina-a-máquina configure as chaves `DFE_API_KEY`,
`DFE_A2A_API_KEY` e `DFE_EVENT_MESH_API_KEY`; a administração usa
`DFE_ADMIN_API_KEY`. `DFE_REQUIRE_AUTH=true` exige as chaves de borda no startup.
Esse requisito preserva a política do core e é independente do login web.

Para parar, preservando o volume fiscal:

```bash
docker compose --env-file packages/dfe/.env -f packages/dfe/compose.yaml down
```

## Pipeline e reprodução dos testes

`.github/workflows/dfe.yml` executa por PR, push em master/main que afete o
produto/core/build, ou despacho manual. Ele valida lint, tipos, componentes,
UI, os dois wheels instalados fora do checkout, Compose, imagem e smoke.
Os wheels de entrega, incluindo a UI, são disponibilizados como artefato do
workflow por sete dias. Não há publicação automática em registro nem deploy.

```bash
uv run python scripts/test_product_isolation.py
uv run python scripts/smoke_dfe_image.py dfe-copilot:local
uv run python scripts/smoke_dfe_image.py dfe-copilot:local --compose
```

O smoke gera usuário, senha, chaves e segredo efêmeros; usa porta dinâmica e
XML sintético. Testa health, acesso protegido, login/cookie, catálogo de plugin,
UI/assets, contexto inválido, XSD do wheel, usuário não root, ausência do shell
IIC e logout. O modo Compose usa projeto/volume aleatórios e os remove ao fim.
Não usa credenciais reais nem substitui serviços existentes.

O smoke não mede diagnóstico com LLM, embeddings, qualidade de recuperação,
SEFAZ ou integração SAP. Esses testes exigem ambiente próprio; o processamento
fiscal determinístico e o fluxo HTTP exercitado são verificados separadamente.
