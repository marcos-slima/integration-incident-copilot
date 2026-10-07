# Getting Started — Integration Incident Copilot

> Do zero ao primeiro diagnóstico em menos de 10 minutos.

---

## Pré-requisitos

| Requisito | Versão mínima | Verificar |
|---|---|---|
| Python | 3.12+ | `python3 --version` |
| uv | qualquer | `uv --version` |
| Ollama | qualquer | `ollama --version` |
| Docker (opcional) | 24+ | `docker --version` |

---

## 1. Clone e configure o ambiente

```bash
git clone https://github.com/marcos-slima/integration-incident-copilot.git
cd integration-incident-copilot
cp .env.example .env
uv sync
```

### Senhas exigidas pelo compose

O `docker-compose.yml` usa `${VAR:?...}` para `POSTGRES_PASSWORD`,
`GRAFANA_PASSWORD` e `NEO4J_PASSWORD`, e o Compose interpola **todos** os
serviços, inclusive os de perfis inativos. No `.env.example` as três estão
comentadas: sem defini-las, **qualquer** `docker compose` (até `up -d qdrant`)
falha com `required variable POSTGRES_PASSWORD is missing a value`. Gere as
três antes do primeiro comando do compose.

Gere também `LLM_CREDENTIALS_MASTER_KEY` (chave Fernet): com `DATABASE_URL`
configurada a API **não sobe** sem ela (DA-60), e no modo container o
compose sempre injeta `DATABASE_URL`.

```bash
for v in POSTGRES_PASSWORD GRAFANA_PASSWORD NEO4J_PASSWORD; do
  echo "$v=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')" >> .env
done
echo "LLM_CREDENTIALS_MASTER_KEY=$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" >> .env
docker compose config -q   # sem saída = interpolação ok
```

---

## 2. Baixe os modelos

```bash
ollama pull qwen3-coder-next:latest
ollama pull nomic-embed-text
```

---

## 3. Suba a infraestrutura

A infraestrutura vem do `docker-compose.yml` **deste repositório** — não de
nenhum diretório externo. Sem perfil, só `api` e `qdrant` sobem; o resto é
opt-in:

| Perfil | Serviços |
|---|---|
| (nenhum) | `api`, `qdrant` |
| `observability` | `postgres`, `grafana`, `reporter` |
| `async` | `redis`, `worker` (só têm efeito com `REDIS_URL` no `.env`) |
| `graphrag` | `neo4j` |
| `container-ollama` | `ollama` |
| `mailpit` / `messagepit` | `mailpit` / `messagepit` |

```bash
# Qdrant (obrigatório) + Postgres/Grafana (persistência de incidentes, opcional)
docker compose --profile observability up -d qdrant postgres grafana

# GraphRAG (opt-in; sem isto o app roda normal, só sem grafo)
docker compose --profile graphrag up -d neo4j
```

Sobe: Qdrant (`localhost:6333`), Postgres (`localhost:5432`), Neo4j
(`localhost:7474`, só com o perfil `graphrag`), Grafana (`localhost:3001`).

Com Postgres, aplique as migrations antes de rodar a aplicação. O Alembic lê
`DATABASE_URL` do `.env` — use a mesma senha gerada no passo 1:

```bash
echo "DATABASE_URL=postgresql+asyncpg://iic:$(grep ^POSTGRES_PASSWORD= .env | cut -d= -f2-)@127.0.0.1:5432/iic" >> .env
uv run alembic upgrade head
```

---

## 4. Indexe a base de conhecimento

```bash
uv run python -m app.rag.ingest --target incidents --reset
```

A raiz primária da base de incidentes é `data/knowledge_base/` (recursiva).
Enquanto ela não existir ou não tiver nenhum `.md`/`.pdf`/`.epub`, o ingest
usa o fallback `data/sample_docs/` (os casos de exemplo do repositório) —
é o que acontece num clone novo. O agente consulta essa base local, não a
internet (a busca web é um fallback opt-in, `WEB_SEARCH_ENABLED`).

---

## 5. Escolha como rodar a aplicação

As duas rotas usam a **mesma** infraestrutura do passo 3. O que muda é
apenas onde o processo Python roda — e o `.env` precisa refletir isso.

### 5a. Nativo, para debug passo a passo (recomendado para estudo)

```bash
uv run uvicorn app.main:app --reload
```

Acesse http://localhost:8000 e use os breakpoints de
`.vscode/launch.json` ("Debug: FastAPI (uvicorn)") — o código roda na sua
máquina, com o debugger do VS Code anexado.

Exige no `.env` os hosts **da sua máquina**, nunca nomes de serviço:

```bash
QDRANT_URL=http://127.0.0.1:6333
DATABASE_URL=postgresql+asyncpg://iic:SENHA@127.0.0.1:5432/iic
NEO4J_URI=bolt://127.0.0.1:7687     # só se GRAPH_RAG_ENABLED=true
```

### 5b. Em container, para deploy

```bash
docker compose --profile observability --profile graphrag up -d
```

Acesse http://localhost:8000. Aqui o `.env` **não** define os hosts: o
`x-common-env` do `docker-compose.yml` injeta `QDRANT_URL`, `DATABASE_URL`
e `NEO4J_URI` com os nomes internos (`qdrant`, `postgres`, `neo4j`) como
variáveis de ambiente, que têm precedência sobre o `.env` montado no
container. O container não enxerga o loopback do host, então `127.0.0.1`
só vale no modo nativo.

O Ollama é o do host, via `OLLAMA_HOST=http://host.docker.internal:11434`
(fixo em `x-common-env`). Isso só funciona se o Ollama nativo escutar em
`0.0.0.0` — por padrão o serviço systemd escuta só em `127.0.0.1` e recusa
o container. Ver [DEPLOY.md](DEPLOY.md), seção 3-B, passo 3.

Se precisar de um Postgres **externo** ao compose (RDS, Cloud SQL, BTP),
defina `CONTAINER_DATABASE_URL` em vez de `DATABASE_URL`; o mesmo vale para
`CONTAINER_NEO4J_URI`.

---

## 6. Como expandir a base de conhecimento

O agente só sabe o que você ensinar. Cada documento em `data/knowledge_base/`
é um caso de troubleshooting que o agente pode recuperar e usar no diagnóstico.

> Ao criar o primeiro arquivo em `data/knowledge_base/`, o fallback
> `data/sample_docs/` deixa de ser lido. Para manter os casos de exemplo,
> copie-os junto: `mkdir -p data/knowledge_base && cp data/sample_docs/*.md data/knowledge_base/`.

### Estrutura de um documento

```markdown
# [Título do Problema] — [Sistema]

## Sintoma
O que o usuário observa: código de erro, transação SAP, comportamento.

## Causas comuns
- Causa mais frequente
- Segunda causa mais comum

## Diagnóstico
1. Primeiro passo de investigação
2. Segundo passo

## Resolução típica
Ação concreta para resolver. Inclua transações SAP e critério de validação.
```

### Exemplo — novo documento

Crie o arquivo dentro de `data/knowledge_base/` (raiz primária da base):
data/knowledge_base/bapi_authorization_failure.md

```markdown
# Falha de Autorização em BAPI — SAP ABAP

## Sintoma
Chamada RFC a uma BAPI retorna AUTHORIZATION_FAILURE.
O usuário técnico tem acesso RFC no SM59, mas a BAPI falha.

## Causas comuns
- Usuário RFC sem objeto de autorização específico da BAPI
- Role do usuário RFC não contém a transação equivalente
- Usuário bloqueado por tentativas inválidas de logon (SU01)

## Diagnóstico
1. Executar a BAPI via SE37 com o usuário RFC em modo debug
2. Verificar o log de autorização via SU53 após a falha
3. Checar bloqueio do usuário via SU01

## Resolução típica
Adicionar o objeto de autorização do SU53 ao role do usuário RFC
via PFCG. Após ajuste, testar novamente via SE37.
```

Depois de criar o arquivo, reindexe:

```bash
uv run python -m app.rag.ingest --target incidents --reset
```

A partir do próximo diagnóstico, o agente considera o novo caso.

### Boas práticas

- Use termos técnicos exatos: RFC_COMM_FAILURE, SM59, BD87, status 51
- Descreva o sintoma como o usuário descreveria ao abrir um chamado
- Um documento por problema — não agrupe problemas não relacionados
- Documente cada incidente resolvido — vira base de conhecimento da equipe
- Formatos suportados: .md (recomendado), .pdf e .epub

---

## 7. Conectores reais

Por padrão todos os conectores operam em modo mock.
Para conectar a sistemas reais, configure no .env:

### OData / SAP Gateway
```
ODATA_SERVICE_URL=https://sistema.sap.com/sap/opu/odata/sap/API_SALES_ORDER_SRV
ODATA_OAUTH_TOKEN_URL=https://xsuaa.authentication.sap.hana.ondemand.com/oauth/token
ODATA_CLIENT_ID=client-id
ODATA_CLIENT_SECRET=secret
```

### RFC / SAP ABAP
```
SAP_ASHOST=sistema.sap.com
SAP_SYSNR=00
SAP_CLIENT=100
SAP_USER=RFC_USER
SAP_PASSWORD=senha
LD_LIBRARY_PATH=/usr/local/sap/nwrfcsdk/lib
```

### ServiceNow
```
SERVICENOW_INSTANCE_URL=https://instancia.service-now.com
SERVICENOW_USERNAME=admin
SERVICENOW_PASSWORD=senha
```

---

## 8. Provedores LLM alternativos

Antes de trocar o provider, quatro pré-requisitos:

1. **Dependência.** `langchain-openai` é o extra `openai` (vale também para
   Azure). No modo nativo: `uv sync --extra openai`. A imagem Docker já o
   inclui.
2. **Soberania de dados.** O default é `DATA_SOVEREIGNTY_MODE=strict` com
   `SENSITIVITY_DEFAULT=confidential`: todo incidente é confidencial e só vai
   para provider local — o gateway **nega** o cloud. Para liberar, as duas
   variáveis juntas (fail-closed), com a ORIGIN real de destino:
   ```bash
   DATA_SOVEREIGNTY_MODE=cloud_with_dlp
   CONFIDENTIAL_ALLOWED_ORIGINS=https://api.openai.com   # ou https://openrouter.ai, https://recurso.openai.azure.com
   ```
   Numa demo sem dado sensível, `SENSITIVITY_DEFAULT=public` também libera
   (só para incidente sem dado real de conector nem PII declarada). Matriz em
   vigor: `GET /llm/policy`.
3. **Modo container.** `LLM_PROVIDER` e `OLLAMA_HOST` são fixos no
   `x-common-env` do `docker-compose.yml` e sobrepõem o `.env` — mudar o
   `.env` não troca o provider. Crie um `docker-compose.override.yml` (o
   Compose o lê automaticamente) e mantenha as credenciais no `.env`:
   ```yaml
   services:
     api:
       environment:
         LLM_PROVIDER: openai   # ou azure_openai
   ```
   Repita o bloco para `worker` se usar o perfil `async`. Depois:
   `docker compose up -d api`.
4. **Embeddings sem GPU.** Sem Ollama, os embeddings também precisam de
   outra fonte: `EMBEDDING_BACKEND=fastembed` (em processo). Trocar o backend
   muda o espaço vetorial — reindexe com `--reset` (passo 4).

```bash
# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o

# Azure OpenAI
LLM_PROVIDER=azure_openai
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://recurso.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=gpt-4o

# OpenRouter (qwen3-coder-next via API, sem GPU local)
LLM_PROVIDER=openai
OPENAI_API_KEY=chave-openrouter
OPENAI_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=qwen/qwen3-coder-next
EMBEDDING_BACKEND=fastembed
```

---

## Resolução de problemas

**libsapnwrfc.so not found**
```bash
export LD_LIBRARY_PATH=/usr/local/sap/nwrfcsdk/lib:$LD_LIBRARY_PATH
```

**`required variable POSTGRES_PASSWORD is missing a value`** (ou
`GRAFANA_PASSWORD`/`NEO4J_PASSWORD`) em qualquer `docker compose`
Faltam as senhas no `.env` — ver passo 1, "Senhas exigidas pelo compose".

**Qdrant connection refused**
```bash
docker compose up -d qdrant
```

**`could not translate host name "postgres"` (modo container)**
O `.env` está apontando para `127.0.0.1` e o container precisa do nome do
serviço. Apague `DATABASE_URL`/`NEO4J_URI` do `.env`: o compose injeta os
hosts internos sozinho (ver passo 5b).

**Postgres nativo recusa a conexão**
O `.env` precisa de `127.0.0.1:5432`, não `postgres:5432` — `postgres` é o
nome do serviço dentro da rede do compose e não resolve na máquina.

**Modelo não encontrado**
```bash
ollama pull qwen3-coder-next:latest && ollama pull nomic-embed-text
```

**Confiança sempre baixa**
```bash
uv run python -m app.rag.ingest --target incidents --reset
```

---

*Integration Incident Copilot · github.com/marcos-slima/integration-incident-copilot*
