# Deploy do Integration Incident Copilot — todos os cenários

Documento único de implantação. Substitui a necessidade de juntar `GETTING_STARTED.md`,
`DEPLOY.md` e `deploy/kyma/README.md`: aqui estão todos os cenários, cada um com o
próprio passo a passo e **o status honesto do que já foi validado**.

| Cenário | Status | Seção |
|---|---|---|
| A. Avaliação local, interested party (≤ 15 min, sem infra pesada) | Validado | [A](#cenário-a-avaliação-local-para-interested-party) |
| B. Desenvolvimento local, app nativo + infra em container | Validado | [B](#cenário-b-desenvolvimento-local-app-nativo--infra-em-container) |
| C. Tudo em container (Docker Compose) | Validado | [C](#cenário-c-tudo-em-container-docker-compose) |
| D. Produção com autenticação obrigatória | Validado (mesmo código de C) | [D](#cenário-d-produção-com-autenticação-obrigatória) |
| E. SAP BTP Kyma Runtime | **Não validado** contra cluster real | [E](#cenário-e-sap-btp-kyma-runtime--não-validado) |
| F. SAP BTP Cloud Foundry | **Não implementado** — sem manifest | [F](#cenário-f-cloud-foundry--não-implementado) |
| G. Kubernetes genérico (EKS/AKS/GKE/ICP) | **Não validado** — Kyma é o único bundle | [G](#cenário-g-kubernetes-genérico) |
| H. Cloud gerenciado (Cloud Run, ECS, Container Apps) | **Não implementado** | [H](#cenário-h-cloud-gerenciado) |
| I. On-premise / datacenter do cliente | **Não documentado** — sem guidance | [I](#cenário-i-on-premise--datacenter-do-cliente) |

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

## Cenário A — Avaliação local para interested party

**Objetivo:** alguém que nunca viu o projeto longe de um diagnóstico real, no
navegador, em uma máquina sem Docker.

**Tempo:** 10 a 15 min. **Pré-requisitos:** Python 3.12+, `uv`, ~10 GB livre, Ollama.

Este é o cenário para **não** usar Docker: exigir Docker e abertura de rede é o que faz
uma avaliação ser abandonada na metade. O modo nativo com infra em container é o
melhor equilíbrio, mas se Docker não estiver disponível, o projeto **roda só com
Ollama e Qdrant** — Postgres, Neo4j, Redis, Grafana são todos opcionais.

### A.1. Dependências

```bash
git clone <url-do-repo> && cd integration-incident-copilot
curl -LsSf https://astral.sh/uv/install.sh | sh

curl -fsSL https://ollama.com/install.sh | sh     # se ainda não tiver
uv sync
```

### A.2. Modelo de linguagem (o passo que mais demora)

```bash
ollama serve &                                    # se não for system service
ollama pull qwen3-coder-next:latest               # canônico (DA-12)
```

O modelo canônico é `qwen3-coder-next:latest` (MoE 80B/3B ativo, 262K ctx), escolhido
por paridade 10/10 no promptfoo contra `qwen2.5-coder:32b` (DA-4/8/12). Em máquina
com pouca RAM, use um modelo menor e **declare isso na avaliação** — o modelo muda a
qualidade da resposta.

### A.3. Configuração mínima

```bash
cp .env.example .env
```

Para avaliação, o default funciona. Ajuste apenas o embedding se usar um modelo
diferente de `nomic-embed-text` (é o default e o que a base foi indexada com).

### A.4. Subir a base de conhecimento

A base de **incidentes** é pequena — 15 documentos, 69 chunks, minutos. A **reference
library** são 21 GB / 2.112 arquivos e leva ~1 dia — **não tente** para uma avaliação:

```bash
PATH="$PWD/.venv/bin:$PATH" uv run python -m app.rag.ingest --target incidents
```

> **Consequência que precisa ser dita ao interested party:** sem a reference library, o
> fallback RAG (DA-17) não tem o que buscar, e o diagnóstico se apoia apenas nos 15
> documentos curados de `data/sample_docs/`. É funcional e é assim que o CI de
> qualidade roda, mas é um subconjunto.

### A.4b. Colocar os seus próprios documentos (opcional)

O target `incidents` procura **`data/knowledge_base/`** primeiro e só usa
`data/sample_docs/` como *fallback* quando ela não existe ou está vazia
(`app/rag/ingest.py::TARGETS`). Para avaliar com o acervo do seu cliente em vez do
curado do projeto, basta colocar os arquivos em `data/knowledge_base/` (formatos
aceitos: `.md`, `.pdf`, `.epub`) e rodar a ingestão. O aviso no log diz qual dos dois
foi usado — confira, porque ele não é erro.

### A.5. Subir a API

```bash
PATH="$PWD/.venv/bin:$PATH" uv run uvicorn app.main:app --reload --port 8000
```

### A.6. Abrir e autenticar

- **UI**: http://localhost:8000/ (o frontend é servido de `static/dist`)
- **Swagger**: http://localhost:8000/docs

No primeiro boot, o log **imprime as chaves geradas** (eles são efêmeras neste modo):

```bash
# o aviso vem em nível ALTO; pegue o valor
grep -i "gerada automaticamente" /tmp/saude.log   # ou o terminal
```

Para a UI web, o **login é fail-closed e exige usuário no `.env`** (DA-55). Sem
`WEB_UI_USERS` configurado, `/auth/login` responde **401 sempre** — inclusive para o
admin. O formato é `usuario:pbkdf2_sha256.<iteracoes>.<salt_hex>.<hash_hex>`:

```bash
# gerar o hash da senha
uv run python -c "from app.auth import hash_password; print(hash_password('sua senha'))"

# .env
WEB_UI_USERS=operador:pbkdf2_sha256.600000.<salt_hex>.<hash_hex>
```

O separador é **ponto**, nunca `$` — o Docker Compose interpola `$` dentro de
variáveis. `WEB_UI_USERS` é o *bootstrap* do operador e continua valendo **junto** com
o banco, então o operador nunca fica trancado fora por causa do `web_users`.

### A.7. Verificar que está de pé

```bash
curl -s localhost:8000/health | jq .status   # liveness: sempre 200 se o processo existe
curl -s localhost:8000/ready  -o /dev/null -w '%{http_code}\n'   # readiness: 200 ou 503
```

`/health` **nunca** chama dependências externas — é liveness. Se Qdrant ou Ollama
estiverem fora, `/health` continua `ok` e **`/ready` devolve 503**. Use `/ready` para
saber se o RAG está de fato utilizável.

### A.8. Fazer um diagnóstico real

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

### A.9. O checklist honesto antes de mostrar a alguém

Não omita nenhum destes quando apresentar:

- [ ] **"tem conector" ≠ "foi validado".** Só 4 conectores rodaram contra sistema real:
      **RFC** (ABAP Cloud Trial), **ServiceNow** (PDI), **Salesforce** (Dev Edition) e
      **CAP** (BTP Trial). **OData, Workday, Ariba, SuccessFactors e PO/PI nunca
      foram validados** contra instância real, e o **API Management tem schema
      especulativo** — endpoint assumido por analogia, não confirmado na documentação.
      A fonte é `docs/ARCHITECTURE.md`, seção da matriz.
- [ ] Sem `reference_library`, o RAG é o subconjunto curado (A.4).
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

O `Dockerfile` copia **apenas** `data/sample_docs/`, e o compose monta esse diretório
como volume read-only. **A reference library (21 GB) não está na imagem nem em volume.**

Consequência: num deploy em container, o RAG funciona com os documentos curados de
incidentes, e o fallback `reference_library` da DA-17 **não tem corpus**. Para tê-lo,
monte a reference library e ingira contra o Qdrant do ambiente:

```bash
docker compose exec api python -m app.rag.ingest --target reference
```

> Herdado da Decisão 1: o `--reset-state` para trocar de Qdrant também vale aqui.

### C.6. Limitação registrada

O `Dockerfile` **não foi construído de fato** no ambiente de desenvolvimento (Docker
não estava disponível). Ele foi revisado e corrigido, e o serviço `api` sobe e responde
em execução real local — mas trate "build de imagem em CI" como um passo não
verificado end-to-end e construa uma vez antes de confiar no pipeline.

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

## Cenário H — Cloud gerenciado

**Não implementado.** Sem manifest, sem config, sem validação para Cloud Run, ECS,
Azure Container Apps ou equivalente.

O que impede o caminho "mais fácil" é real: sem Ollama nativo na máquina, o
diagnóstico depende de um LLM remoto, o que aciona a política de soberania
(`DATA_SOVEREIGNTY_MODE`), o AI Gateway e a cobrança de tokens. **Essa combinação não
está documentada nem validada** — é a lacuna mais relevante deste inventário.

---

## Cenário I — On-premise / datacenter do cliente

**Não há guidance.** Para um portfólio SAP este costuma ser o cenário de destino, e é
o que mais falta no repositório.

Pontos que um documento precisaria cobrir e hoje não são cobertos em lugar nenhum:

- **Ollama é o gargalo.** O modelo canônico é MoE 80B/3B. Dimensionar RAM/VRAM para ele
  é um requisito de dimensionamento que este repositório **não** publica.
- **BTP gerenciado vs. self-hosted.** `docs/TCO_SAP_AI_CORE_VS_SELF_HOSTED.md` discute
  TCO, mas não é um guia de implantação.
- **Rede.** O Compose assume portas simples; on-premise costuma ter proxy, TLS
  intermediário e firewall. Nenhum foi testado atrás de proxy.
- **SAP on-premise (PO/PI, HANA, ABAP).** O conector PO/PI existe mas usa **API não
  pública** e não foi validado.

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
