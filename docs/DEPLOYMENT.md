# Deploy do Integration Incident Copilot — todos os cenários

Documento único de implantação. Substitui a necessidade de juntar `GETTING_STARTED.md`,
`DEPLOY.md` e `deploy/kyma/README.md`: aqui estão todos os cenários, cada um com o
próprio passo a passo e **o status honesto do que já foi validado**.

| Cenário | Status | Seção |
|---|---|---|
| A. Avaliação local para interessado (app em ~3 min; modelo 4,5–51 GB) | Validado | [A](#cenário-a--avaliação-local-para-interessado) |
| B. Desenvolvimento local, app nativo + infra em container | Validado | [B](#cenário-b--desenvolvimento-local-app-nativo--infra-em-container) |
| C. Tudo em container (Docker Compose) | Validado | [C](#cenário-c--tudo-em-container-docker-compose) |
| D. Produção com autenticação obrigatória | Validado (mesmo código de C) | [D](#cenário-d--produção-com-autenticação-obrigatória) |
| E. SAP BTP Kyma Runtime | **Não validado** contra cluster real | [E](#cenário-e--sap-btp-kyma-runtime-não-validado) |
| F. SAP BTP Cloud Foundry | **Não implementado** — sem manifest | [F](#cenário-f--cloud-foundry-não-implementado) |
| G. Kubernetes genérico (EKS/AKS/GKE/ICP) | **Não validado** — Kyma é o único bundle | [G](#cenário-g--kubernetes-genérico) |
| H. Cloud gerenciado + LLM remoto | **Não implementado** — o que falta está mapeado | [H](#cenário-h--cloud-gerenciado--llm-remoto-soberania-de-dados) |
| I. On-premise / datacenter do cliente | Guía com sizing, rede e sequência | [I](#cenário-i--on-premise--datacenter-do-cliente) |

> **Sobre os cenários E a I:** eles existem no inventário porque alguém vai perguntar
> "e se eu rodar no Cloud Run?". A resposta honesta é que o repositório **não traz** o
> que essa resposta exigiria. Dizer isso faz parte do deploy doc; omitir faz o
> documento parecer mais completo do que é.

---

## Antes de qualquer cenário: as 3 decisões

Quase todo erro de implantação deste projeto vem de uma destas três. Decida antes
de continuar.

### Decisão 1 — o `.env` é **por modo**, e é a armadilha nº 1

Os dois modos locais usam **o mesmo** `docker-compose.yml`. Muda só **onde o processo
Python roda**, e isso muda o que você escreve no `.env`:

| Onde o app roda | `DATABASE_URL` usa | `QDRANT_URL` usa |
|---|---|---|
| **Nativo** (venv na sua máquina) | loopback — `127.0.0.1:5432` | `127.0.0.1:6333` |
| **Container** (compose) | nome de serviço — `postgres:5432` | `qdrant:6333` |

Motivo: `postgres` e `qdrant` **só resolvem dentro da rede do compose**. São nomes de
serviço, não hosts. No modo nativo, `DATABASE_URL=…@postgres:5432/iic` dá
`could not translate host name "postgres"`, e a falha parece ser de rede quando é de
configuração.

Para apontar um container a um banco **externo** (RDS, Cloud SQL, BTP), use
`CONTAINER_DATABASE_URL` e `CONTAINER_NEO4J_URI` — elas não colidem com as variáveis
do modo nativo. Ver `.env.example:337`.

### Decisão 2 — Ollama é **nativo** ou containerizado, e é a Decisão 1 de novo

O `docker-compose.yml` tem um serviço `ollama`, mas ele está no profile
`container-ollama`, **opt-in**, e o compose aponta para o host via
`host.docker.internal` quando você não o usa.

- Host tem Ollama nativo (`/usr/local/bin/ollama serve`) → é o padrão, mais simples.
- Máquina sem Ollama → suba o serviço e ligue o profile.

### Decisão 3 — a chave de autenticação é **fixa** ou **efêmera**

Chaves vazias no `.env` **não** desabilitam a auth. O startup gera uma chave aleatória
**por processo**, e ela muda a cada restart. Isso serve para "clonar e rodar", e é
inaceitável em produção.

Por isso existe `REQUIRE_AUTH` (`.env.example:217`):

- `REQUIRE_AUTH=false` (default) → gera chave efêmera e **avisa em log ALTO**.
- `REQUIRE_AUTH=true` → o processo **recusa subir** se `API_KEY`, `A2A_API_KEY` ou
  `EVENT_MESH_API_KEY` estiverem vazias. Não há geração automática.

> ⚠️ **`REQUIRE_AUTH` não cobre `ADMIN_API_KEY`.** A verificação em
> `app/main.py::_ensure_api_keys_configured` lista as 3 chaves de máquina; a admin é
> tratada separadamente em `app/admin/security.py::ensure_admin_key_configured`, que
> **também só avisa** e gera efêmera. Com `REQUIRE_AUTH=true` e `ADMIN_API_KEY` vazia, o
> `/admin` sobe com chave que só existe no log de startup. **Configure `ADMIN_API_KEY`
> manualmente** — é a única forma de fixá-la.

---

## Cenário A — Avaliação local para interessado

**Objetivo:** alguém que nunca viu o projeto longe de um diagnóstico real, no
navegador, numa máquina de trabalho.

**Tempo e disco — leia antes de prometer tempo a alguém:**

| Etapa | Tempo | Disco |
|---|---|---|
| App + Qdrant no ar | ~3 min | ~1 GB |
| Baixar o modelo canônico | **10 min a horas** (depende da banda) | **51 GB** |
| `uv sync` (build da venv) | ~2 min | ~2 GB |

⚠️ O passo caro é o **modelo**, não a aplicação. Se for para uma demo de 15 minutos,
**não baixe o canônico** — use `qwen3-coder:latest` (18 GB) ou `tev1:latest` (4,5 GB) e
**declare na avaliação que o modelo não é o canônico**. Prometer "15 min" e descobrir
51 GB de download na frente do cliente é o jeito rápido de queimar a primeira
impressão; a tabela de I.1 tem o tamanho real de cada um.

**Pré-requisitos:** Python 3.12+, `uv`, Ollama, e **Qdrant**.

Este é o cenário para **não** usar Docker na aplicação: exigir Docker e abertura de rede
é o que faz uma avaliação ser abandonada na metade. O modo nativo com infra em container
é o melhor equilíbrio, mas se Docker não estiver disponível, o projeto **roda sem
Docker nenhum** — Postgres, Neo4j, Redis e Grafana são todos opcionais. **Qdrant não é
opcional**: é onde o RAG vive (veja A.2).

### A.1. Dependências

```bash
git clone <url-do-repo> && cd integration-incident-copilot
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

### A.2. Qdrant (o passo que costuma faltar)

Sem Qdrant não há RAG: o `retriever` sobe degradado e o diagnóstico sai sem âncora
documental. Ele é a única peça que precisa ficar de pé.

```bash
# opção 1 — Docker, se tiver (um container só)
docker run -d --name qdrant -p 6333:6333 -p 6334:6334 \
  -v qdrant_storage:/qdrant/storage qdrant/qdrant

# opção 2 — binário nativo, se a máquina não tem Docker (é o motivo deste cenário)
curl -L https://github.com/qdrant/qdrant/releases/latest/download/qdrant-x86_64-unknown-linux-gnu.tar.gz \
  | tar xz && ./qdrant
```

Confirme antes de seguir:

```bash
curl -s localhost:6333/healthz        # {"status":"ok"}
```

> ⚠️ Se **outro** Qdrant já estiver na `6333` desta máquina, use outra porta — elem
> `QDRANT_HOST_PORT` para o compose e `QDRANT_URL` para o app nativo **juntos**,
> senão um aponta para o outro.

### A.3. Modelo de linguagem (o passo que mais demora)

```bash
ollama serve &                                    # se não for system service
ollama pull qwen3-coder-next:latest               # canônico (DA-12) — 51 GB
```

O modelo canônico é `qwen3-coder-next:latest` (MoE 80B/3B ativo, 262K ctx), escolhido
por paridade 10/10 no promptfoo contra `qwen2.5-coder:32b` (DA-4/8/12). Em máquina
sem RAM para 51 GB, use um modelo menor e **declare isso na avaliação** — o modelo muda a
qualidade da resposta (I.1 tem o tamanho de cada um).

O embedding (`nomic-embed-text`, default) também precisa estar baixado, senão o RAG
degrada na primeira requisição.

### A.4. Configuração mínima

```bash
cp .env.example .env
```

Para avaliação, o default funciona. Ajuste apenas o embedding se usar um modelo
diferente de `nomic-embed-text` (é o default e o que a base foi indexada com).

### A.5. Subir a base de conhecimento

A base de **incidentes** é pequena — 15 documentos, 69 chunks, minutos. A **reference
library** são 21 GB / 2.112 arquivos e leva ~1 dia — **não tente** para uma avaliação:

```bash
PATH="$PWD/.venv/bin:$PATH" uv run python -m app.rag.ingest --target incidents
```

> **Consequência que precisa ser dita ao interested party:** sem a reference library, o
> fallback RAG (DA-17) não tem o que buscar, e o diagnóstico se apoia apenas nos 15
> documentos curados de `data/sample_docs/`. É funcional e é assim que o CI de
> qualidade roda, mas é um subconjunto.

### A.5b. Colocar os seus próprios documentos (opcional)

O target `incidents` procura **`data/knowledge_base/`** primeiro e só usa
`data/sample_docs/` como *fallback* quando ela não existe ou está vazia
(`app/rag/ingest.py::TARGETS`). Para avaliar com o acervo do seu cliente em vez do
curado do projeto, basta colocar os arquivos em `data/knowledge_base/` (formatos
aceitos: `.md`, `.pdf`, `.epub`) e rodar a ingestão. O aviso no log diz qual dos dois
foi usado — confira, porque ele não é erro.

### A.6. Subir a API

Antes de subir, **fixe a chave** no `.env` — senão ela é gerada aleatoriamente a cada
startup e você não tem como chamar a API depois (veja A.7):

```dotenv
API_KEY=chave-local-de-avaliacao
```

```bash
PATH="$PWD/.venv/bin:$PATH" uv run uvicorn app.main:app --reload --port 8000
```

### A.7. Abrir e autenticar

- **UI**: http://localhost:8000/ (o frontend é servido de `static/dist`)
- **Swagger**: http://localhost:8000/docs

Com `API_KEY` fixada, os testes abaixo já funcionam. **Se você não a fixou**, o processo
anuncia uma chave efêmera no log, em `WARNING`, no startup — leia no terminal em que a
API subiu (ou no arquivo para onde você redirecionou). Não existe arquivo de log
padrão; e a chave **muda a cada restart**.

Para a UI web, o **login é fail-closed e exige usuário no `.env`** (DA-55). Sem
`WEB_UI_USERS` configurado, `/auth/login` responde **401 sempre** — inclusive para o
admin. O formato é `usuario:pbkdf2_sha256.<iteracoes>.<salt_hex>.<hash_hex>`, e várias
entradas separam por **vírgula**:

```bash
# gerar o hash da senha
uv run python -c "from app.auth import hash_password; print(hash_password('sua senha'))"

# .env
WEB_UI_USERS=operador:pbkdf2_sha256.600000.<salt_hex>.<hash_hex>
```

O separador é **ponto**, nunca `$` — o Docker Compose interpola `$` dentro de variáveis, e
um `$` aqui vira variável-inexistente: **o salt SUMIA** e ninguém conseguia logar. `WEB_UI_USERS`
é o *bootstrap* do operador e continua valendo **junto** com o banco, então o operador
nunca fica trancado fora por causa do `web_users`.

### A.8. Verificar que está de pé

```bash
curl -s localhost:8000/health | jq .status   # liveness: sempre 200 se o processo existe
curl -s localhost:8000/ready  -o /dev/null -w '%{http_code}\n'   # readiness: 200 ou 503
```

`/health` **nunca** chama dependências externas — é liveness. Se Qdrant ou Ollama
estiverem fora, `/health` continua `ok` e **`/ready` devolve 503**. Use `/ready` para
saber se o RAG está de fato utilizável.

### A.9. Fazer um diagnóstico real

```bash
curl -s localhost:8000/diagnose \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <a chave que apareceu no log>" \
  -d '{
    "incident_description": "IDoc de pedido de compra fica em status 03 no WE02 desde a manhã, sem reprocessar",
    "connector_type": "odata"
  }' | jq '{status, agent_domain, is_grounded, evidence_strength}'
```

O esperado é um diagnóstico com `evidence_strength` e `is_grounded` calculados
**deterministicamente** (DA-15/16), nunca por autoavaliação do LLM.

### A.10. O checklist honesto antes de mostrar a alguém

Não omita nenhum destes quando apresentar:

- [ ] **"tem conector" ≠ "foi validado".** Só 4 conectores rodaram contra sistema real:
      **RFC** (ABAP Cloud Trial), **ServiceNow** (PDI), **Salesforce** (Dev Edition) e
      **CAP** (BTP Trial). **OData, Workday, Ariba, SuccessFactors e PO/PI nunca
      foram validados** contra instância real, e o **API Management tem schema
      especulativo** — endpoint assumido por analogia, não confirmado na documentação.
      A fonte é `docs/ARCHITECTURE.md`, seção da matriz.
- [ ] Sem `reference_library`, o RAG é o subconjunto curado (A.5).
- [ ] O Rule Engine (DA-33, 21 regras) determinístico **aciona antes do LLM** em
      vários casos e encerra sem chamar o modelo. Quando isso acontece, `prompt_version`
      e `prompt_digest` vêm **NULL** de propósito (DA-53): um diagnóstico sem LLM não
      foi produzido por prompt nenhum.
- [ ] As chaves são efêmeras neste cenário. Cada restart, novas chaves.

---

## Cenário B — Desenvolvimento local: app nativo + infra em container

Melhor das duas metades: você **debugga no seu IDE** (13 configs em
`.vscode/launch.json`) e a infraestrutura é real, isolada e derrubável.

### B.1. Infra via compose

```bash
docker compose --profile observability up -d qdrant postgres grafana redis
docker compose --profile graphrag      up -d neo4j     # opt-in
```

Serviços e profiles:

| Serviço | Profile | Porta (default) |
|---|---|---|
| `qdrant` | *(sem profile)* | 6333 |
| `postgres` | `observability` | 5432 |
| `grafana` | `observability` | 3001 |
| `reporter` | `observability` | — |
| `redis` | `async` | 6379 (bind `127.0.0.1`) |
| `worker` | `async` | — |
| `neo4j` | `graphrag` | 7474 / 7687 |
| `ollama` | `container-ollama` | 11434 |
| `api` | *(sem profile)* | 8000 |

### B.2. `.env` no modo **nativo** — loopback

```dotenv
DATABASE_URL=postgresql+asyncpg://iic:iic@127.0.0.1:5432/iic
QDRANT_URL=http://127.0.0.1:6333
NEO4J_URI=bolt://127.0.0.1:7687
```

### B.3. Neo4j: a senha só vale na primeira vez

`NEO4J_AUTH` só é lido **na primeira inicialização do volume**. Trocar a senha no
`.env` depois **não** troca a senha do banco — o container sobe com a senha antiga e
`verify_connectivity()` falha com uma mensagem que não parece ter relação com a senha.

```bash
docker compose --profile graphrag down
docker volume rm integration-incident-copilot_neo4j_data   # ⚠️ apaga o grafo
```

Sem `NEO4J_PASSWORD` no `.env`, o serviço sobe com o placeholder
`neo4j/REQUIRED_SET_IN_ENV` e falha no `verify_connectivity()`.

`GRAPH_RAG_ENABLED=false` é o default e é a flag real (o `CLAUDE.md` antigo citava
`USE_GRAPH_RAG`, que não existe).

### B.4. Dois Qdrant na mesma máquina

O compose publica em `${QDRANT_HOST_PORT:-6333}`. Se outro stack já usar a `6333`:

```dotenv
QDRANT_HOST_PORT=6335
```

E o app precisa apontar para a porta correspondente (`QDRANT_URL=…:6335`).

⚠️ **Armadilha de estado:** `data/.ingest_state_<target>.json` é **um arquivo por
target, sem URL dentro** (chave = `hash:filename`). Ele marca "já processado" sem
registrar **onde**. Rodar a ingestão contra um Qdrant e depois contra outro faz o
segundo run **pular tudo** e o destino ficar vazio — **sem erro**. Trocar de destino
exige `--reset-state` **e** gravar o estado contra a URL pretendida.

### B.5. Subir

```bash
PATH="$PWD/.venv/bin:$PATH" uv run uvicorn app.main:app --reload
```

Migrações (se houver Postgres):

```bash
PATH="$PWD/.venv/bin:$PATH" uv run alembic upgrade head
```

### B.6. Debug no VS Code

13 launch configs prontas. As mais úteis: **Debug: FastAPI (uvicorn)**,
**Debug: graph.py (caso IDoc travado)** (exercita RFC + regra 51) e
**Debug: pytest (só unitários, rápido)**.

---

## Cenário C — Tudo em container (Docker Compose)

### C.1. Build

```bash
docker compose build
```

O `Dockerfile` é **multi-stage**: constrói o frontend React/Vite a partir do
`frontend/` e copia o resultado para `static/dist`. **Não há passo manual de
`npm run build`** — `static/dist/` está no `.gitignore`, mas o build do container
gera tudo. (Um `git clone` limpo seguido de `docker build` já produz a imagem
completa.)

A imagem final roda **não-root** (UID 1000), usa `uv sync --frozen` contra o
`uv.lock` commitado (build reproduzível) e chama `.venv/bin/uvicorn` direto — **não**
`uv run`, que ressincroniza o ambiente a cada start e derruba o container em rede
restrita.

### C.2. `.env` no modo **container** — nomes de serviço

```dotenv
DATABASE_URL=postgresql+asyncpg://iic:iic@postgres:5432/iic
QDRANT_URL=http://qdrant:6333
NEO4J_URI=bolt://neo4j:7687
```

O compose injeta `postgres`, `qdrant` e `neo4j` em `x-common-env` por default, então
apontar para `127.0.0.1` aqui **quebra o container**.

### C.3. Estratégia de LLM

Duas opções:

- **A) Sem Ollama nativo na máquina** → o serviço `ollama` entra:
  ```bash
  docker compose --profile container-ollama up -d
  ```
- **B) Ollama nativo já rodando** → o compose aponta para `host.docker.internal`.
  Só subir o serviço `ollama` se for usar de fato; deixar os dois é custo dobrado de RAM.

### C.4. Subir

```bash
docker compose --profile observability up -d
curl -s localhost:8000/health | jq .status
```

### C.5. Base de conhecimento — leia antes

O `Dockerfile` copia **apenas** `data/sample_docs/` (linha 49), e o compose monta esse
diretório como volume read-only. **A reference library (21 GB) não está na imagem nem em
volume.**

Consequência: num deploy em container, o RAG funciona com os documentos curados de
incidentes, e o fallback `reference_library` da DA-17 **não tem corpus**. Pior: o target
`reference` tem `fallback_dir: None`, então `resolve_source_dir()` devolve um caminho
inexistente e `find_files()` **não ingere nada — sem erro**. Um deploy com o fallback
DA-17 silenciosamente inerte é indistinguível de um deploy sem a reference.

Para habilitar a reference, monte o corpus no container **e persista o state**:

```bash
# 1. override que monta o data/ do host (traz reference_library E o
#    .ingest_state_reference.json — sem o state, cada run reingere tudo)
cat > docker-compose.ingest.yml <<'YAML'
services:
  api:
    volumes:
      - ./data:/app/data
YAML

# 2. roda a ingestao como container descartavel (nao precisa da API no ar;
#    'run' sobe o Qdrant por causa do depends_on)
docker compose -f docker-compose.yml -f docker-compose.ingest.yml run --rm \
  api python -m app.rag.ingest --target reference
```

Dois detalhes que decidem se isso funciona:

- **O caminho é `/app/data/reference_library`, não `/corpus`.** `BASE_DIR` é
  `parents[2]` de `app/rag/ingest.py` (`app/rag/ingest.py:77`), ou seja `/app`. Montar em
  outro lugar faz o script procurar um diretório que não existe e voltar sem erro.
- **O override é necessário, não opcional.** O compose monta só `./data/sample_docs`;
  sem o `-f docker-compose.ingest.yml`, o container não enxerga o corpus nem o state.

> Herdado da Decisão 1: o `--reset-state` para trocar de Qdrant também vale aqui — e o
> state precisa ser regravado contra a URL pretendida, senão a run pula tudo e o destino
> fica sem os 21 GB esperados, sem erro.

### C.6. O build — validado, e o que ele revelou

O `Dockerfile` **foi construído de fato** e o caminho foi medido ponta a ponta (build →
`up -d` → `/health` → `/ready` → `/diagnose`):

| Medição | Valor |
|---|---|
| Contexto de build | **1,87 MB** |
| Imagem resultante | 6,73 GB |
| `/health`, `/ready` | 200, todos os conectores `mock` (sem credenciais — esperado) |
| `/diagnose` real | 200 em **~45 s**, com a ingestão da reference disputando o mesmo Ollama |

O número que importa é o **contexto de 1,87 MB** — e ele quase foi 22 GB. O
`.dockerignore` não excluía `data/reference_library/` (21 GB de PDFs que a imagem
**nunca usa**: o `Dockerfile` só faz `COPY` de `data/sample_docs/`). Sem a exclusão, o
daemon recebe o repo inteiro antes de o build começar — o primeiro `docker build` de um
cliente demoraria horas e poderia estourar disco. Corrigido no `.dockerignore`; se um
fork antigo reclamar de "transferring context: 22GB", é isso.

Dois avisos honestos sobre o que a medição **não** cobre:

- O `/diagnose` de 45 s aconteceu **com a ingestão da reference ativa na mesma
  máquina** — é o pior caso doméstico, não o número de produção. Sem disputa, espera-se
  bem menos; meça o seu.
- O build foi validado nesta máquina. Um pipeline de CI (registry, buildx multi-arch,
  cache remoto) continua sendo responsabilidade de quem implanta.

---

## Cenário D — Produção com autenticação obrigatória

Mesma base do Cenário C, com o que muda para produção.

### D.1. Chaves fixas, nunca efêmeras

```dotenv
REQUIRE_AUTH=true
API_KEY=<token forte>
A2A_API_KEY=<token forte>
EVENT_MESH_API_KEY=<token forte>
ADMIN_API_KEY=<token forte>      # NÃO coberto pelo REQUIRE_AUTH — configure à mão
```

Gere tokens com `python -c "import secrets;print(secrets.token_urlsafe(32))"`.

### D.2. Registro de modelos (fail-closed)

```dotenv
LLM_REGISTRY_DB=true
LLM_CREDENTIALS_MASTER_KEY=<Fernet>
METERING_ENABLED=true
```

O registro dirige o runtime (resolve modelo, `base_url` e chave do banco em vez do
`.env`) e é **fail-closed**: registry vazio para a origem **rejeita a chamada**.
Gere a master key com:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Credenciais de provedor ficam cifradas em repouso com Fernet (DA-47); a master key
nunca entra em runtime, só no `.env`/secret.

### D.3. Soberania de dados (fail-closed, por origem real)

```dotenv
DATA_SOVEREIGNTY_MODE=strict        # ou cloud_with_dlp
CONFIDENTIAL_ALLOWED_ORIGINS=<lista>
```

`strict` não envia nada para fora. A capacidade é definida **por origem real**, não
pelo rótulo (DA-43/45): `openai` apontando para Gemini e para `api.openai.com` não são
o mesmo destino. Inspecione o que está valendo agora:

```bash
curl -s localhost:8000/llm/policy -H "X-API-Key: $API_KEY" | jq
```

### D.4. O que ligar

```dotenv
PROMETHEUS_ENABLED=true      # expõe /metrics (opt-in; default false)
```

`/metrics` só existe com `PROMETHEUS_ENABLED=true` **e** `prometheus_client` instalado.
Não há Prometheus no compose — expor a métrica e coletar é decisão sua.

Grafana (profile `observability`) já vem com 4 dashboards provisionados. Suas 45
queries são validadas contra Postgres real por `scripts/validate_dashboards.py` —
rode antes de confiar num painel.

### D.5. AMQP / Event Mesh (opcional)

```dotenv
AMQP_ENABLED=true      # perfil `async`: sobe redis + worker
```

Consumidor AMQP 1.0 (python-qpid-proton, DA-40) para Solace Cloud / SAP Event Mesh.

### D.6. Checklist de produção

- [ ] `REQUIRE_AUTH=true` e as 4 chaves fixas no secret (não no `.env` do repo)
- [ ] `LLM_REGISTRY_DB` + master key Fernet
- [ ] `DATA_SOVEREIGNTY_MODE` coerente com a política de dados do cliente
- [ ] TLS termination na frente da API
- [ ] `/ready` responde 200; `/health` só para liveness
- [ ] `scripts/quality_gate.py` passa
- [ ] `scripts/validate_dashboards.py` roda contra o Postgres real
- [ ] `reference_library` **decidida conscientemente** (C.5): sem ela o fallback DA-17 é inerte

---

## Cenário E — SAP BTP Kyma Runtime (**não validado**)

Bundle completo em `deploy/kyma/` (10 arquivos: `deployment.yaml`, `service.yaml`,
`apirule.yaml`, `hpa.yaml`, `configmap.yaml`, `namespace.yaml`, `worker.yaml`,
`kustomization.yaml`, `secret.example.yaml`, `README.md`).

> ⚠️ **Nenhum manifest foi aplicado a um cluster Kyma real.** Não há cluster acessível no
> ambiente de desenvolvimento. Também **nenhum build/push de imagem Docker foi testado**.
> Isto é uma configuração declarada, não uma implanta verificada.

### E.1. Aplicar

```bash
# 1. build + push da imagem para registry acessível pelo cluster
# 2. deployment.yaml: troque <REGISTRY>/<TAG>
# 3. apirule.yaml: troque <CLUSTER_DOMAIN>
# 4. segredos: NUNCA commitar preenchido — copie secret.example.yaml
cp deploy/kyma/secret.example.yaml /tmp/secret.yaml   # edite com valores reais
kubectl apply -f /tmp/secret.yaml

# 5. o resto
kubectl apply -k deploy/kyma/
kubectl rollout status deploy/<nome> -n <namespace>
```

### E.2. Pré-requisitos que o bundle **não** resolve

- **Qdrant** — o `configmap.yaml` aponta para `http://qdrant:6333` no mesmo namespace,
  mas o bundle **não implanta o Qdrant**. Sem ele, o Pod sobe (as probes de
  `/health` passam) e **`/diagnose` falha ao consultar o RAG**. Use o Helm chart
  oficial ou um serviço gerenciado.
- **Neo4j** — continua opt-in; ligar exigiria implantá-lo fora do bundle.
- **Indexação** — rode o ingest contra o Qdrant do cluster **uma vez**, depois que ele
  estiver de pé.
- **`accessStrategy: noop`** no `APIRule` assume que a API key por endpoint (DA-18/DA-23)
  basta. Migrar para `jwt`/XSUAA seria a evolução natural, mas foi
  **escopo explicitamente descartado** nesta fase. Não anuncie XSUAA como implementado.

### E.3. Conferir o schema do `APIRule`

O schema do CRD mudou mais de uma vez na história do Kyma. O `apiVersion:
gateway.kyma-project.io/v1beta1` é o correto até onde vai o conhecimento deste projeto.
**Confira contra o cluster alvo antes de aplicar:**

```bash
kubectl explain apirule.spec
```

---

## Cenário F — Cloud Foundry (**não implementado**)

Não existe `manifest.yml` no repositório, e não há referência a Cloud Foundry em
nenhum arquivo de código, manifest ou config. Não há também binding de Destination
Service, XSUAA ou service key — o repositório não consome nenhum desses serviços BTP.

Se você precisar, o caminho é reusar o `Dockerfile` do Cenário C e escrever os
manifests. Não há atalho, e tratar Kyma como "o deploy BTP" é o erro a evitar: Kyma é
**Kubernetes**, não Cloud Foundry.

---

## Cenário G — Kubernetes genérico

O bundle de `deploy/kyma/` é declaradamente Kyma (`APIRule` é um CRD do Kyma). Para
EKS/AKS/GKE/ICP você reescreve: `APIRule` → `Ingress` ou `Gateway`, e
`Service`/`Deployment`/`ConfigMap` são portables.

O que **é** portable e vale reusar: o `Dockerfile` (multi-stage, não-root, sem
dependência de runtime), o `HEALTHCHECK` (`/health`, com `--start-period` porque a
primeira indexação é lenta) e a separação liveness/readiness.

O `HPA` do bundle referencia métricas — confira se o cluster tem o metrics-server.

Nada disso foi executado. Trate como trabalho, não como configuração.

---

## Cenário H — Cloud gerenciado + LLM remoto (soberania de dados)

Não há manifest para Cloud Run, ECS ou Container Apps — e a ausência é deliberada,
porque o caminho não é só "empacotar differently": **sem Ollama local, o diagnóstico
passa a depender de um LLM remoto**, o que aciona política de soberania, AI Gateway e
cobrança. Nada disso está validado junto. O que segue é o que precisa ser decidido
para esse caminho existir — não um passo a passo validado.

### H.1. As duas decisões que travam esse cenário

**1. O LLM é remoto?** Se `LLM_PROVIDER != ollama`, todo incidente — incluindo PII
redigida, mas não todo o contexto — sai da sua rede. É uma decisão de contrato com o
cliente, não de configuração.

**2. A identidade (embedding) é a mesma?** O RAG é sensível a isso. A capacidade é
declarada **por origem real, não por rótulo** (DA-43/45): `openai` apontando para
Gemini e para `api.openai.com` **não são o mesmo destino**. Um destino que você não
reconheceu é **recusado** (fail-closed). Antes de trocar qualquer coisa:

```bash
curl -s localhost:8000/llm/policy -H "X-API-Key: $API_KEY" | jq
```

### H.2. Modos de soberania

```dotenv
DATA_SOVEREIGNTY_MODE=strict            # default: nada sai
DATA_SOVEREIGNTY_MODE=cloud_with_dlp    # só o que a allowlist autorizar
CONFIDENTIAL_ALLOWED_ORIGINS=<origens>
```

- **`strict`** (default) — nada vai para LLM cloud. A allowlist é **ignorada**. É a
  única escolha defensável sem agreement de tratamento de dados.
- **`cloud_with_dlp`** — `CONFIDENTIAL_ALLOWED_ORIGINS` passa a valer, **junto** com
  este modo. O comentário do código é explícito: a allowlist é fail-closed e vale
  *somente* com `cloud_with_dlp`.

Não existe terceiro modo "permissive" — e um typo como `local_only` não é
silenciosamente tratado como cloud: o campo é `Literal`, então **o processo não sobe**.

`SENSITIVITY_DEFAULT=confidential` (default conservador) é o que mantém o modo
`strict` efetivo sem configuração extra.

### H.3. Credenciais de provedor

Com LLM remoto a credencial passa a ser segredo gerenciado, não linha de `.env`:

```dotenv
LLM_REGISTRY_DB=true
LLM_CREDENTIALS_MASTER_KEY=<Fernet>     # ver D.2
```

O registro é **fail-closed**: registry vazio para a origem **rejeita a chamada**, em vez
de degradar. Configure pelo `/admin` (Models) — a chave nunca passa pelo navegador em
claro.

### H.4. Custo e limites

`METERING_ENABLED=true` persiste tokens **reais** (de `usage_metadata`, não estimados;
DA-48) em `llm_usage`, com `DATABASE_URL` configurado. O AI Gateway tem **budget** e
**circuit breaker** (DA-26/41).

Para um diagnóstico, tudo isso significa que o custo é **por incidente**, não por
mês — e que `seed=42` e `temperature=0.0` dão reproduibilidade, mas **não**
determinismo de custo: o número de tokens depende do tamanho do contexto recuperado.

### H.5. O que falta para este cenário existir

Não é escrever um `Dockerfile` (já existe) — é:

- [ ] Definir e testar a política de soberania com um cliente real
- [ ] Validar que o reranker cross-encoder também tem caminho remoto, ou fica local
- [ ] Publicar um custo por diagnóstico a partir de `llm_usage`
- [ ] Confirmar se a indexação da reference library pode ocorrer **dentro** da rede

---

## Cenário I — On-premise / datacenter do cliente

Para um portfólio SAP, este costuma ser o cenário de destino. É o que **menos** existia
neste repositório — e o que mais precisa de decisão explícita antes da primeira
instalação, porque o gargalo é hardware e rede, não software.

### I.1. Decisão 1: dimensionamento do LLM local

O modelo canônico `qwen3-coder-next:latest` é MoE 80B/3B. Tamanho **real em disco**,
medido nesta máquina com `ollama list`:

| Modelo | Disco | Quando usar |
|---|---|---|
| `qwen3-coder-next:latest` | **51 GB** | canônico (DA-12); exige a máquina mais bem dimensionada |
| `qwen3-coder:latest` | 18 GB | alternativa menor; **perde a paridade 10/10 do promptfoo** — declare isso |
| `tev1:latest` | 4,5 GB | máquinas sem GPU; só para smoke test, não para avaliação |
| `nomic-embed-text:latest` | 274 MB | embedding denso (obrigatório, default) |
| `bge-m3:latest` | 1,2 GB | encoder sparse/BM25 |

O modelo **não** é negociável por variável de ambiente sem consequência: `llm_model` é
texto livre (`app/config.py:179`), mas a escolha do modelo muda a qualidade da resposta,
e a comparação que justifica o canônico foi medida com o promptfoo (DA-4/8/12).

⚠️ **51 GB de pesos não é o mesmo que 51 GB de RAM.** Para resident, some VRAM
disponível. Com `strict` como soberania (default), **não há como fugir do LLM local** —
se a máquina não couber, o caminho é `cloud_with_dlp` com contrato de dados, ou
aceitar um modelo menor e documentar a troca.

Requisito adicional: os dois encoders (denso e sparse) precisam estar **baixados antes**
do primeiro diagnóstico, ou o `RAG` degrada em silêncio.

### I.2. Decisão 2: a base de conhecimento é grande e é local

A reference library são **21 GB / 2.112 arquivos** e a ingestão leva ~1 dia em máquina
de trabalho. Em on-premise isso é decisão de capacidade, não de passo:

- **Espaço em disco**: corpus + Qdrant. O Qdrant guarda os vetores; ~500 mil chunks
  de 768 dimensões ocupam alguns GB, mas reserve para o growth.
- **Tempo**: a ingestão é limitada por embedding na CPU. Com GPU, ordens de grandeza
  mais rápido. **Não faça isso na janela de produção.**
- **Escaneados**: ~3% dos PDFs não têm texto extraível (digitalizados). Sem OCR eles
  entram no state e produzem **zero chunks** — silenciosamente. Se o acervo do cliente
  vier em boa parte digitalizado, **OCR é obrigatório** e precisa acontecer **antes**
  da ingestão, ou o state precisa ser reprocessado.

Como rodar (com object store externo, veja I.3):

```bash
uv run python -m app.rag.ingest --target reference
```

### I.3. Decisão 3: a topologia da rede

O compose assume portas simples, loopback e nomes de serviço. On-premise costuma ter
proxy, TLS intermediário e firewall. **Nada foi testado atrás de proxy.**

Levantamento antes de instalar:

| Item | Pergunta que decide a config |
|---|---|
| URLs externas | O SAP é on-prem (`RFC`/`OData` para dentro) ou BTP? muda o conector |
| Proxy de saída | Há `HTTPS_PROXY`? o container não herda o proxy do host |
| Resolução de nomes | `postgres`, `qdrant`, `neo4j` **só resolvem na rede do compose** |
| Porta do Qdrant | Se outro stack usar `6333`, defina `QDRANT_HOST_PORT` e `QDRANT_URL` juntos |
| TLS | O compose não termina TLS; a terminação fica na frente |

⚠️ **Proxy é a lacuna mais provável.** `QDRANT_URL`, `NEO4J_URI` e `OLLAMA_HOST`
apontam para o serviço interno; mas **pull do modelo, `npm ci` e `uv sync` precisam de
saída**. Num ambiente air-gapped, o build **não funciona** — pré-carregue os modelos
Ollama e as wheels, ou construa a imagem em ambiente com rede e transporte por
registry interno.

### I.4. O que muda em relação ao container (C)

Praticamente o mesmo código. O que muda:

1. **`.env` em modo container** — `postgres:5432`, `qdrant:6333`, `neo4j:7687` (nomes de
   serviço), **não** `127.0.0.1`.
2. **Neo4j**: a senha do volume só vale na primeira subida; se o volume já existiu, a
   senha do `.env` não muda a do banco (ver B.3).
3. **Chaveiras fixas** para o cliente — `REQUIRE_AUTH=true` e as 4 chaves (ver D.1,
   incluindo a `ADMIN_API_KEY`, que o `REQUIRE_AUTH` não cobre).
4. **Soberania**: `DATA_SOVEREIGNTY_MODE=strict` é o default e o que o on-prem pede.
5. **Sem `reference_library` na imagem** — ela é ingerida, não copiada (ver C.5).

### I.5. Conectores on-premise: o que esperar

Para um cliente SAP on-premise, o que **tem** validação real:

- **RFC** — validado contra ABAP Cloud Developer Trial. Em on-prem, é o conector mais
  direto para `BAPI_IDOC_STATUS` e friends (o `BAPI_IDOC_STATUS` **não** existe no
  Trial; precisa de função Z ou landscape real).

O que **não** foi validado e você deve tratá-lo como hipótese:

- **OData** — nunca validado contra instância real. É o caminho mais comum para
  S/4HANA e o mais provável de encontrar diferença de schema.
- **PO/PI** — usa **API não publicada** (Message Monitor fora do Help Portal, varia por
  patch). A URL da fachada (`PO_BASE_URL`) é informação do cliente.
- **Workday, Ariba, SuccessFactors** — nunca validados contra instância real.
- **API Management** — schema **especulativo**, não confirmado na documentação.

A matriz completa, com o que falta em cada, é `docs/ARCHITECTURE.md`. O gate
`connector_validation_matrix` garante que ela **cobre** todo conector registrado — mas
atestar que a afirmação existe **não** é atestar que ela é verdadeira.

### I.6. Primeira instalação num cliente novo (sequência sugerida)

1. **Levantar rede e sizing** (I.1, I.3) antes de instalar qualquer coisa.
2. Subir **Qdrant** primeiro (é a dependência do RAG), e `/ready` reflecting.
3. Subir a **API** em modo de estudo (`REQUIRE_AUTH=false`) e validar `/health`.
4. **Indexar `incidents`** (minutos) e validar um `/diagnose` ponta a ponta. Não pular
   para reference antes de isso funcionar.
5. **Indexar `reference`** (I.2) em janela própria.
6. Só então fixar chaves e `REQUIRE_AUTH=true`.
7. Registrar no `/admin` o sistema integrado do cliente (DA-49/50) — é o que faz a
   correlação incidente↔sistema funcionar.

---

## Apêndice A — Comandos de ingestão

```bash
uv run python -m app.rag.ingest --target incidents   # 15 docs / 69 chunks, minutos
uv run python -m app.rag.ingest --target reference   # 2.112 arquivos / 21 GB, ~1 dia
uv run python -m app.rag.ingest --target all
```

| Flag | Efeito |
|---|---|
| `--limit N` | processa só N arquivos |
| `--exclude PADRAO` | exclui por glob (repetível) |
| `--reset-state` | limpa **só** o estado local; não apaga a collection |
| `--reset-collection` | apaga e recria a collection **e** limpa o estado |

⚠️ **Arquivos com erro não entram no state** — voltam como pendentes no próximo run.
Um `408 Request Timeout` esporádico é normal e se resolve relançando **sem** `--reset`.

> **Antes de `--reset-collection` num corpus grande:** a collection `reference_library`
> é dense-only de propósito (medido: o dense já traz o termo exato no top-1 — ver
> `app/rag/retriever.py`). Habilitar híbrido exigiria recriá-la e reindexar tudo sem
> ganhar recall.

## Apêndice B — Verificações antes de confiar

```bash
PATH="$PWD/.venv/bin:$PATH" uv run pytest tests/ -m "not integration"   # unitário
PATH="$PWD/.venv/bin:$PATH" uv run python scripts/quality_gate.py        # invariantes
PATH="$PWD/.venv/bin:$PATH" uv run python scripts/validate_dashboards.py # 45 queries
```

O quality gate (DA-51) valida dataset de avaliação, corpus, invariante do reranker,
configs do promptfoo, documentação e alcançabilidade de conectores — inclusive que
todo conector registrado aparece na matriz de `docs/ARCHITECTURE.md`. Ele **não** cobre
a `reference_library` (só `data/sample_docs/`).

## Apêndice C — Mapa de documentos

Este documento é o índice de implantação. Os demais respondem outras perguntas:

| Documento | Pergunta que responde |
|---|---|
| [`GETTING_STARTED.md`](GETTING_STARTED.md) | Do zero ao primeiro diagnóstico |
| [`USER_GUIDE.md`](USER_GUIDE.md) | Como usar: headers, telas, endpoints |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Como funciona + **matriz de validação dos conectores** |
| [`DEPLOY.md`](DEPLOY.md) | Detalhe do caminho Docker Compose |
| [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) | Falha por sintoma |
| [`QUALITY_GATES.md`](QUALITY_GATES.md) | O que o gate cobre — e o que não cobre |
| [`INGEST_REFERENCE.md`](INGEST_REFERENCE.md) | Indexação da reference library |
| [`deploy/kyma/README.md`](../deploy/kyma/README.md) | Detalhe do bundle Kyma |
